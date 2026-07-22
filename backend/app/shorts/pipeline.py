"""The Shorts pipeline.

Small on purpose. The long pipeline renders a documentary from images, narration
and music; this one does not render anything — it *cuts*. The finished MP4
already contains the mixed narration, the music, the burned-in subtitles and the
in-scene transitions, so the only jobs here are to take the right spans out of
it, lay the 16:9 picture on a vertical black canvas, and prove the result.

Stages:

  1. verify the source still matches its manifest
  2. cut each contiguous group (frame-accurate, cached)
  3. concatenate the cuts in the user's order
  4. compose onto the 1080x1920 canvas
  5. validate the output with ffprobe
  6. publish atomically into ``exports/shorts/``
  7. clean up

Two rules inherited from ``render/ffmpeg.py`` hold throughout: commands are
argument lists, never shell strings, and no user-supplied text ever reaches a
filtergraph. Every number in the graph below is computed here from validated
integers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import ErrorCode, RenderError
from app.render.ffmpeg import CancelledRender, FFmpegRunner, base_output_args, progress_args
from app.shorts.encode import (
    SEGMENT_VIDEO_ARGS,
    SHORT_AUDIO_ARGS,
    segment_cache_name,
    short_video_args,
)
from app.shorts.manifest import RenderManifest, verify_source
from app.shorts.models import (
    ShortArtifact,
    ShortGroupPlan,
    ShortManifest,
    ShortPhase,
    ShortPlan,
    ShortRequest,
)
from app.shorts.validate import ShortValidation, fit_geometry, validate_short
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join

logger = logging.getLogger("evb.shorts.pipeline")

ProgressCallback = Callable[[ShortPhase, float, str], None]

PHASE_WEIGHTS: dict[ShortPhase, float] = {
    ShortPhase.VALIDATE_SOURCE: 0.08,
    ShortPhase.PLAN: 0.02,
    ShortPhase.CUT_SEGMENTS: 0.38,
    ShortPhase.CONCAT: 0.07,
    ShortPhase.COMPOSE: 0.32,
    ShortPhase.VALIDATE_OUTPUT: 0.08,
    ShortPhase.PUBLISH: 0.03,
    ShortPhase.CLEANUP: 0.02,
}

#: Hex colours only. The layout model already enforces this; asserting it again
#: here is what makes "nothing user-supplied reaches the filtergraph" checkable
#: at the one place a colour is interpolated.
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass
class ShortsArtifacts:
    video: Path
    manifest: Path | None = None
    log: Path | None = None


@dataclass
class ShortsResult:
    artifacts: ShortsArtifacts
    plan: ShortPlan
    validation: ShortValidation
    short_manifest: ShortManifest
    warnings: list[str] = field(default_factory=list)
    reused_segments: int = 0
    cut_segments: int = 0


class ShortsPipeline:
    """Builds one Short. Create a new instance per job."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        manifest: RenderManifest,
        request: ShortRequest,
        plan: ShortPlan,
        settings: Settings | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        job_id: str = "",
    ) -> None:
        self.paths = paths
        self.manifest = manifest
        self.request = request
        self.plan = plan
        self.settings = settings or get_settings()
        self.runner = FFmpegRunner(self.settings)
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self.job_id = job_id

        self.layout = request.layout
        self.fps = manifest.profile.fps
        #: Resolved once: every later comparison and FFmpeg argument uses it.
        self.source_video = safe_join(paths.exports, manifest.source.filename)
        self.log: list[str] = []
        self.warnings: list[str] = list(plan.warnings)
        self._completed_weight = 0.0
        self._last_reported = 0.0
        self._reused = 0
        self._cut = 0

    # --- plumbing ---------------------------------------------------------

    @property
    def short_id(self) -> str:
        return self.plan.cache_key

    @property
    def output_name(self) -> str:
        return f"{self.manifest.project_slug}-short-{self.short_id}.mp4"

    def _work_dir(self) -> Path:
        # Deliberately on the same filesystem as exports/, so publishing the
        # finished file is a single atomic rename.
        name = self.job_id or self.short_id
        return self.paths.shorts_cache / "work" / name

    def _emit(self, phase: ShortPhase, fraction: float, message: str) -> None:
        if self.on_progress is None:
            return
        weight = PHASE_WEIGHTS.get(phase, 0.02)
        overall = min(1.0, self._completed_weight + weight * max(0.0, min(1.0, fraction)))
        overall = max(overall, self._last_reported)
        self._last_reported = overall
        self.on_progress(phase, overall, message)

    def _finish_phase(self, phase: ShortPhase) -> None:
        self._completed_weight = min(1.0, self._completed_weight + PHASE_WEIGHTS.get(phase, 0.02))

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CancelledRender("short cancelled")

    def _record(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > 20_000:
            del self.log[:5_000]

    # --- the run ----------------------------------------------------------

    async def run(self) -> ShortsResult:
        work = self._work_dir()
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        try:
            # 1. The source must still be exactly the file the manifest describes.
            self._emit(ShortPhase.VALIDATE_SOURCE, 0.0, "Checking the source render")
            verify_source(self.manifest, self.source_video, settings=self.settings)
            self._record(
                f"[source] {self.manifest.source.filename} "
                f"{self.manifest.source.width}x{self.manifest.source.height} "
                f"@{self.fps}fps, sha256 ok"
            )
            self._finish_phase(ShortPhase.VALIDATE_SOURCE)

            # 2. Log the plan. It was computed and validated before the job was
            #    ever queued; nothing here recomputes a boundary.
            self._emit(ShortPhase.PLAN, 0.0, "Preparing the cut list")
            for group in self.plan.groups:
                self._record(
                    f"[cut {group.index}] sections {group.numbers} "
                    f"{group.start_seconds:.3f}s -> {group.end_seconds:.3f}s "
                    f"({group.duration_seconds:.3f}s, "
                    f"{group.preserved_transitions} transition(s) preserved)"
                )
            self._finish_phase(ShortPhase.PLAN)

            joined = await self._materialize(work)

            # 4. Lay the horizontal picture on the vertical canvas.
            output = work / "short.mp4"
            await self._compose(joined, output)
            self._finish_phase(ShortPhase.COMPOSE)

            # 5. Prove it.
            self._emit(ShortPhase.VALIDATE_OUTPUT, 0.0, "Validating the Short")
            validation = validate_short(
                output,
                expected_width=self.layout.width,
                expected_height=self.layout.height,
                expected_fps=self.fps,
                expected_duration=self.plan.total_duration_seconds,
                source_width=self.manifest.source.width,
                source_height=self.manifest.source.height,
                cut_count=len(self.plan.groups),
                settings=self.settings,
            )
            if not validation.passed:
                raise RenderError(
                    ErrorCode.OUTPUT_VALIDATION_FAILED,
                    "FFmpeg finished, but the Short failed validation and was not published.",
                    details=validation.format_failures(),
                    suggestion=(
                        "The partial file was discarded rather than listed as complete. "
                        "Retry; if it repeats, the details above name every assertion."
                    ),
                )
            self.warnings.extend(validation.warnings)
            self._finish_phase(ShortPhase.VALIDATE_OUTPUT)

            # 6. Publish atomically.
            self._emit(ShortPhase.PUBLISH, 0.0, "Publishing the Short")
            artifacts, short_manifest = self._publish(output, validation)
            self._finish_phase(ShortPhase.PUBLISH)

            self._emit(ShortPhase.CLEANUP, 0.0, "Cleaning up")
            self._finish_phase(ShortPhase.CLEANUP)

            return ShortsResult(
                artifacts=artifacts,
                plan=self.plan,
                validation=validation,
                short_manifest=short_manifest,
                warnings=self.warnings,
                reused_segments=self._reused,
                cut_segments=self._cut,
            )
        finally:
            # A cancelled or failed run must never leave a half-written MP4
            # anywhere the history could pick it up as complete.
            shutil.rmtree(work, ignore_errors=True)

    # --- stages -----------------------------------------------------------

    async def _materialize(self, work: Path) -> Path:
        """Produce one horizontal video holding every group, in order.

        A single group needs no intermediate at all: it is cut straight out of
        the source during the compose pass, which is both faster and one
        generation cleaner.
        """
        if len(self.plan.groups) == 1:
            self._finish_phase(ShortPhase.CUT_SEGMENTS)
            self._finish_phase(ShortPhase.CONCAT)
            self._record("[cut] single contiguous group: cut and composed in one pass")
            return self.source_video

        segments: list[Path] = []
        total = len(self.plan.groups)
        for position, group in enumerate(self.plan.groups):
            self._check_cancelled()
            segments.append(await self._cut_group(group, position, total))
        self._finish_phase(ShortPhase.CUT_SEGMENTS)

        joined = await self._concat(segments, work)
        self._finish_phase(ShortPhase.CONCAT)
        return joined

    async def _cut_group(self, group: ShortGroupPlan, position: int, total: int) -> Path:
        """Cut one contiguous span, frame-accurately.

        Stream copy is only correct when the cut lands on a keyframe, and an
        H.264 export with a two-second GOP rarely does. The default path
        therefore re-encodes near-losslessly, which is what makes the cut land
        on the frame the user chose rather than up to two seconds away. The copy
        path is taken only when ffprobe proves the start is keyframe-aligned.
        """
        target = self._segment_path(group)
        if target.is_file() and target.stat().st_size > 10_000:
            self._reused += 1
            self._record(f"[cut {group.index}] reused cached segment {target.name}")
            self._emit(
                ShortPhase.CUT_SEGMENTS, (position + 1) / total,
                f"Reused cut {position + 1} of {total}",
            )
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial.mp4")
        partial.unlink(missing_ok=True)

        copyable = self._is_keyframe_aligned(group.start_seconds)
        codec_args = (
            ["-c", "copy"]
            if copyable
            else [
                *base_output_args(fps=self.fps),
                *SEGMENT_VIDEO_ARGS,
                *SHORT_AUDIO_ARGS,
            ]
        )
        self._record(
            f"[cut {group.index}] {'stream copy (keyframe aligned)' if copyable else 'near-lossless re-encode'}"
        )

        args = [
            self.settings.require_tool("ffmpeg"),
            "-hide_banner", "-nostdin", "-y", *progress_args(),
            "-ss", f"{group.start_seconds:.6f}",
            "-i", str(self.source_video),
            "-t", f"{group.duration_seconds:.6f}",
            "-map", "0:v:0", "-map", "0:a:0",
            *codec_args,
            "-movflags", "+faststart",
            str(partial),
        ]

        def progress(fraction: float) -> None:
            self._emit(
                ShortPhase.CUT_SEGMENTS,
                (position + fraction) / total,
                f"Cutting {position + 1} of {total}",
            )

        self._emit(ShortPhase.CUT_SEGMENTS, position / total, f"Cutting {position + 1} of {total}")
        await self.runner.run(
            args,
            stage=f"short-cut-{group.index}",
            expected_duration=group.duration_seconds,
            on_progress=progress,
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )
        os.replace(partial, target)
        self._cut += 1
        return target

    async def _concat(self, segments: list[Path], work: Path) -> Path:
        """Join the cuts in selection order, copying streams where possible."""
        self._emit(ShortPhase.CONCAT, 0.0, "Joining the cuts")
        joined = work / "joined.mp4"
        listing = work / "concat.txt"
        listing.write_text(
            "\n".join(_concat_entry(segment) for segment in segments) + "\n", "utf-8"
        )

        ffmpeg = self.settings.require_tool("ffmpeg")
        copy_args = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", *progress_args(),
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart",
            str(joined),
        ]
        try:
            await self.runner.run(
                copy_args,
                stage="short-concat",
                log_sink=self._record,
                cancel_event=self.cancel_event,
            )
            return joined
        except CancelledRender:
            raise
        except RenderError as exc:
            # Codec, timebase or stream-layout mismatch between the cuts. Fall
            # back to a real re-encode rather than shipping a broken join.
            logger.warning("concat copy failed, re-encoding instead: %s", exc.message)
            self._record("[concat] stream-copy join failed; falling back to a re-encode")
            self.warnings.append(
                "The cuts could not be joined by copying streams, so they were re-encoded. "
                "The result is identical in content, just one generation further from the "
                "source."
            )

        joined.unlink(missing_ok=True)
        args = [ffmpeg, "-hide_banner", "-nostdin", "-y", *progress_args()]
        for segment in segments:
            args += ["-i", str(segment)]
        pairs = "".join(f"[{index}:v][{index}:a]" for index in range(len(segments)))
        args += [
            "-filter_complex", f"{pairs}concat=n={len(segments)}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            *base_output_args(fps=self.fps),
            *SEGMENT_VIDEO_ARGS,
            *SHORT_AUDIO_ARGS,
            "-movflags", "+faststart",
            str(joined),
        ]
        await self.runner.run(
            args,
            stage="short-concat-reencode",
            expected_duration=self.plan.total_duration_seconds,
            on_progress=lambda fraction: self._emit(
                ShortPhase.CONCAT, fraction, "Joining the cuts"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )
        return joined

    async def _compose(self, source: Path, output: Path) -> None:
        """Place the horizontal picture centred on the vertical canvas."""
        self._check_cancelled()
        geometry = fit_geometry(
            self.manifest.source.width,
            self.manifest.source.height,
            self.layout.width,
            self.layout.height,
        )
        colour = _ffmpeg_colour(self.layout.background_color)
        self._record(
            f"[compose] {self.manifest.source.width}x{self.manifest.source.height} -> "
            f"{geometry.inner_width}x{geometry.inner_height} centred on "
            f"{self.layout.width}x{self.layout.height} at "
            f"({geometry.offset_x},{geometry.offset_y}), background {colour}"
        )

        # Every value below is an integer this module computed, or a hex colour
        # matched against _HEX_COLOUR. No user text is interpolated.
        graph = (
            f"[0:v]scale={geometry.inner_width}:{geometry.inner_height}:flags=bicubic,"
            f"setsar=1,"
            f"pad={self.layout.width}:{self.layout.height}:"
            f"{geometry.offset_x}:{geometry.offset_y}:color={colour},"
            f"format=yuv420p,fps={self.fps}[v]"
        )

        args = [
            self.settings.require_tool("ffmpeg"),
            "-hide_banner", "-nostdin", "-y", *progress_args(),
        ]
        single_pass = source == self.source_video
        if single_pass:
            group = self.plan.groups[0]
            args += ["-ss", f"{group.start_seconds:.6f}"]
        args += ["-i", str(source)]
        if single_pass:
            args += ["-t", f"{self.plan.groups[0].duration_seconds:.6f}"]

        args += [
            "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a:0",
            *base_output_args(fps=self.fps),
            *short_video_args(),
            *SHORT_AUDIO_ARGS,
            "-movflags", "+faststart",
            str(output),
        ]

        self._emit(ShortPhase.COMPOSE, 0.0, "Building the vertical frame")
        await self.runner.run(
            args,
            stage="short-compose",
            expected_duration=self.plan.total_duration_seconds,
            on_progress=lambda fraction: self._emit(
                ShortPhase.COMPOSE, fraction, f"Building the vertical frame — {fraction * 100:.0f}%"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )

    def _publish(
        self, staged: Path, validation: ShortValidation
    ) -> tuple[ShortsArtifacts, ShortManifest]:
        """Move the validated file into exports, then write its side-cars."""
        destination = self.paths.shorts_exports
        destination.mkdir(parents=True, exist_ok=True)
        final = safe_join(destination, self.output_name)

        short_manifest = ShortManifest(
            short_id=self.short_id,
            project_slug=self.manifest.project_slug,
            filename=final.name,
            cache_key=self.plan.cache_key,
            job_id=self.job_id,
            source_render_id=self.manifest.render_job_id,
            source_video=self.manifest.source.filename,
            source_sha256=self.manifest.source.sha256,
            source_manifest_schema_version=self.manifest.schema_version,
            layout=self.layout,
            width=self.layout.width,
            height=self.layout.height,
            fps=self.fps,
            duration_seconds=self.plan.total_duration_seconds,
            size_bytes=validation.size_bytes,
            sha256=validation.checksum,
            plan=self.plan,
            request=self.request,
            validation=validation.to_dict(),
        )

        # Same filesystem by construction, so this replace is atomic: the
        # exports folder never contains a partially written Short.
        os.replace(staged, final)

        manifest_path = final.with_name(f"{final.stem}.json")
        _atomic_text(manifest_path, short_manifest.model_dump_json(indent=2))

        log_path = final.with_name(f"{final.stem}.log")
        _atomic_text(log_path, "\n".join(self.log))

        logger.info("published short %s (%.2fs)", final.name, short_manifest.duration_seconds)
        return (
            ShortsArtifacts(video=final, manifest=manifest_path, log=log_path),
            short_manifest,
        )

    # --- helpers ----------------------------------------------------------

    def _segment_path(self, group: ShortGroupPlan) -> Path:
        """Content-addressed cache path for one cut."""
        name = segment_cache_name(
            self.manifest.source.sha256,
            group.start_seconds,
            group.end_seconds,
            self.fps,
        )
        return self.paths.shorts_cache / "segments" / name

    def _is_keyframe_aligned(self, start: float) -> bool:
        """Whether ``start`` lands on a keyframe, within half a frame.

        Only then is a stream copy frame-accurate. Anything else takes the
        re-encode path, because being two seconds off is not a trade-off worth
        making for a clip this short.
        """
        if start <= 0.0:
            return True
        try:
            ffprobe = self.settings.require_tool("ffprobe")
            window = 2.0
            result = self.runner._run_sync(  # noqa: SLF001 - same module family
                [
                    ffprobe, "-hide_banner", "-loglevel", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "frame=pts_time,key_frame",
                    "-read_intervals", f"{max(0.0, start - window):g}%+{window * 2:g}",
                    "-print_format", "csv=p=0",
                    str(self.source_video),
                ],
                timeout=60.0,
            )
        except Exception:  # noqa: BLE001 - probing is an optimisation, never fatal
            return False
        if not result.ok:
            return False

        tolerance = 0.5 / self.fps
        for line in result.stdout.splitlines():
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                is_key = parts[0].strip() == "1"
                pts = float(parts[1])
            except ValueError:
                continue
            if is_key and abs(pts - start) <= tolerance:
                return True
        return False


def _concat_entry(path: Path) -> str:
    """One line of an FFmpeg concat list, escaped the way the demuxer expects."""
    escaped = str(path).replace("\\", "\\\\").replace("'", "'\\''")
    return f"file '{escaped}'"


def _ffmpeg_colour(value: str) -> str:
    if not _HEX_COLOUR.match(value):
        raise RenderError(
            ErrorCode.SHORT_INVALID_SELECTION,
            "The background colour is not a valid hex value.",
            details=f"got {value!r}, expected #RRGGBB",
        )
    return f"0x{value[1:].upper()}"


def _atomic_text(target: Path, text: str) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(target)


def artifacts_for(slug: str, artifacts: ShortsArtifacts) -> list[ShortArtifact]:
    """Downloadable side-cars for a finished Short."""
    out: list[ShortArtifact] = []

    def add(kind: str, path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        out.append(
            ShortArtifact(
                kind=kind,
                filename=path.name,
                size_bytes=path.stat().st_size,
                url=f"/api/projects/{slug}/shorts/exports/{path.name}",
            )
        )

    add("video", artifacts.video)
    add("manifest", artifacts.manifest)
    add("log", artifacts.log)
    return out
