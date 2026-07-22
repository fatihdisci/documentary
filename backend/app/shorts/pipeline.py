"""The Shorts pipeline.

Small on purpose. The long pipeline renders a documentary from images, narration
and music; this one does not render anything — it *cuts*. The finished MP4
already contains the mixed narration, the music and the in-scene transitions, so
the only jobs here are to take the right spans out of it, lay the 16:9 picture on
a vertical black canvas, and prove the result.

Stages:

  1. verify the source still matches its manifest
  2. cut each contiguous group (frame-accurate, cached)
  3. concatenate the cuts in the user's order
  4. build the caption track, in ``shorts-native`` mode only
  5. compose onto the 1080x1920 canvas
  6. validate the output with ffprobe
  7. publish atomically into ``exports/shorts/``
  8. clean up

**Which source is cut** is the one thing caption mode changes:

* ``source-burned-in`` — the historical path, unchanged. Cut the finished
  captioned export; the small burned-in captions come through with the picture.
* ``shorts-native`` / ``off`` — cut the render's verified clean master instead,
  which carries identical picture and identical audio and no burned-in captions.
  In native mode the render's own cue data is rebased onto the Short's timeline
  and drawn large, on the vertical canvas, *after* the 16:9 picture is placed.

There is no third possibility. A render without a verified clean master is
rejected in preflight with an actionable message; it is never silently cut from
the captioned export, because that would caption the Short twice.

Two rules inherited from ``render/ffmpeg.py`` hold throughout: commands are
argument lists, never shell strings, and no user-supplied text ever reaches a
filtergraph. Caption text exists only as pixels in a Pillow-drawn PNG. Every
number in the graphs below is computed here from validated integers.
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
from app.shorts.captions import (
    CaptionTrack,
    build_caption_track,
    overlay_steps,
    track_digest,
)
from app.shorts.cues import CueSidecar, RebasedCue, rebase_cues
from app.shorts.manifest import (
    RenderManifest,
    ShortsSourcePackage,
    verify_clean_master,
    verify_source,
)
from app.shorts.models import (
    ShortArtifact,
    ShortCaptionMode,
    ShortCaptionProvenance,
    ShortCaptionStyle,
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
    ShortPhase.CUT_SEGMENTS: 0.36,
    ShortPhase.CONCAT: 0.07,
    ShortPhase.BUILD_CAPTIONS: 0.04,
    ShortPhase.COMPOSE: 0.30,
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
        clean_master: Path | None = None,
        cue_sidecar: CueSidecar | None = None,
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
        self.caption_mode = request.caption_mode
        self.caption_style: ShortCaptionStyle = request.resolved_caption_style()

        #: The verified clean master and its cue data, resolved and checked by
        #: the caller before the job was ever queued. Both are required for any
        #: mode that does not cut the captioned export, and the constructor
        #: refuses the combination rather than degrading to the wrong source.
        self.clean_master = clean_master
        self.cue_sidecar = cue_sidecar
        if self.caption_mode.needs_clean_master and clean_master is None:
            raise RenderError(
                ErrorCode.SHORT_CAPTIONS_UNAVAILABLE,
                "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak "
                "için uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden "
                "oluşturun.",
                details=f"'{self.caption_mode.value}' modu doğrulanmış bir altyazısız kopya ister",
            )

        #: The file this Short is actually cut from. Resolved once: every later
        #: comparison, cache path and FFmpeg argument uses it.
        self.export_video = safe_join(paths.exports, manifest.source.filename)
        self.source_video = clean_master if clean_master is not None else self.export_video
        #: Checksum of whatever ``source_video`` is, for the cut cache.
        self.source_sha256 = (
            manifest.shorts_source.clean_master.sha256
            if clean_master is not None and manifest.shorts_source is not None
            else manifest.source.sha256
        )

        self.log: list[str] = []
        self.warnings: list[str] = list(plan.warnings)
        self._completed_weight = 0.0
        self._last_reported = 0.0
        self._reused = 0
        self._cut = 0
        self._captions: CaptionTrack = CaptionTrack()
        self._rebased: list[RebasedCue] = []

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
            #    For a captioned cut that is the export itself; for a native one
            #    it is the clean master, which the caller verified in full before
            #    queueing and which is re-checked here against the same manifest.
            self._emit(ShortPhase.VALIDATE_SOURCE, 0.0, "Kaynak video kontrol ediliyor")
            if self.clean_master is None:
                verify_source(self.manifest, self.source_video, settings=self.settings)
            else:
                verify_clean_master(
                    self.manifest, self.source_video, settings=self.settings
                )
            self._record(
                f"[source] {self.source_video.name} "
                f"{self.manifest.source.width}x{self.manifest.source.height} "
                f"@{self.fps}fps, sha256 ok "
                f"({'clean master' if self.clean_master else 'captioned export'})"
            )
            self._record(f"[captions] mode {self.caption_mode.value}")
            self._finish_phase(ShortPhase.VALIDATE_SOURCE)

            # 2. Log the plan. It was computed and validated before the job was
            #    ever queued; nothing here recomputes a boundary.
            self._emit(ShortPhase.PLAN, 0.0, "Kesim listesi hazırlanıyor")
            for group in self.plan.groups:
                self._record(
                    f"[cut {group.index}] sections {group.numbers} "
                    f"{group.start_seconds:.3f}s -> {group.end_seconds:.3f}s "
                    f"({group.duration_seconds:.3f}s, "
                    f"{group.preserved_transitions} transition(s) preserved)"
                )
            self._finish_phase(ShortPhase.PLAN)

            joined = await self._materialize(work)

            # 4. Captions, in native mode only.
            await self._build_captions(work)
            self._finish_phase(ShortPhase.BUILD_CAPTIONS)

            # 5. Lay the horizontal picture on the vertical canvas, then draw the
            #    captions over it — on the canvas, not inside the picture.
            output = work / "short.mp4"
            await self._compose(joined, output)
            self._finish_phase(ShortPhase.COMPOSE)

            # 5. Prove it.
            self._emit(ShortPhase.VALIDATE_OUTPUT, 0.0, "Kısa video kontrol ediliyor")
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
                    "Kısa video oluştu ama kontrolden geçemedi ve kaydedilmedi.",
                    details=validation.format_failures(),
                    suggestion=(
                        "Yarım dosya tamamlanmış gibi listelenmedi, silindi. Tekrar deneyin; "
                        "yine olursa yukarıdaki ayrıntılarda hangi kontrolün kaldığı yazıyor."
                    ),
                )
            self.warnings.extend(validation.warnings)
            self._finish_phase(ShortPhase.VALIDATE_OUTPUT)

            # 6. Publish atomically.
            self._emit(ShortPhase.PUBLISH, 0.0, "Kısa video kaydediliyor")
            artifacts, short_manifest = self._publish(output, validation)
            self._finish_phase(ShortPhase.PUBLISH)

            self._emit(ShortPhase.CLEANUP, 0.0, "Temizlik yapılıyor")
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
                f"Hazır parça kullanıldı: {position + 1} / {total}",
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
                f"Kesiliyor: {position + 1} / {total}",
            )

        self._emit(
            ShortPhase.CUT_SEGMENTS, position / total, f"Kesiliyor: {position + 1} / {total}"
        )
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
        self._emit(ShortPhase.CONCAT, 0.0, "Parçalar birleştiriliyor")
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
                "Parçalar doğrudan birleştirilemediği için yeniden kaydedildi. İçerik birebir "
                "aynı; sadece kaynaktan bir adım daha uzak."
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
                ShortPhase.CONCAT, fraction, "Parçalar birleştiriliyor"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )
        return joined

    async def _build_captions(self, work: Path) -> None:
        """Rebase the render's cues onto this Short and draw them.

        Only ``shorts-native`` reaches the drawing. ``off`` deliberately does
        nothing: it already cut the clean master, which has no captions in it.
        """
        if self.caption_mode is not ShortCaptionMode.SHORTS_NATIVE:
            return
        self._check_cancelled()
        assert self.cue_sidecar is not None  # guaranteed by the constructor

        self._emit(ShortPhase.BUILD_CAPTIONS, 0.0, "Altyazılar hazırlanıyor")
        self._rebased = rebase_cues(self.cue_sidecar.cues, self.plan.groups)
        self._record(
            f"[captions] {len(self.cue_sidecar.cues)} source cue(s) -> "
            f"{len(self._rebased)} after clipping to {len(self.plan.groups)} cut(s)"
        )
        if not self._rebased:
            self.warnings.append(
                "Seçtiğiniz bölümlerin hiçbirinde konuşma yok, bu yüzden kısa videoda "
                "altyazı yok. Görüntü ve ses bundan etkilenmedi."
            )
            return

        self._captions = build_caption_track(
            self._rebased,
            self.caption_style,
            canvas_width=self.layout.width,
            canvas_height=self.layout.height,
            output_dir=self.paths.shorts_cache / "caption-cards",
        )
        if self._captions.is_empty:
            return

        first = self._captions.cards[0].card
        self._record(
            f"[captions] {len(self._captions.cards)} card(s) at "
            f"{self._captions.fitted_font_size}px, bottom inset "
            f"{self.caption_style.safe_bottom_inset}px, first card box "
            f"{first.box_width}x{first.box_height} at ({first.box_x},{first.box_y})"
        )

        self._emit(ShortPhase.BUILD_CAPTIONS, 0.4, "Altyazılar çiziliyor")
        if self._captions.should_precompose:
            # A dense track would otherwise mean one FFmpeg input and two filter
            # steps per cue in the compose graph. Bake them into a single
            # transparent video instead, exactly as the long pipeline does for a
            # scene with many cues.
            self._captions.precomposed = await self._precompose_captions(work)
            self._record(
                f"[captions] pre-composited into {self._captions.precomposed.name}"
            )

    async def _precompose_captions(self, work: Path) -> Path:
        """Bake every caption card into one transparent QT RLE track."""
        digest = track_digest(
            self._captions,
            canvas_width=self.layout.width,
            canvas_height=self.layout.height,
        )
        cache = self.paths.shorts_cache / "caption-tracks"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"captions-{digest}.mov"
        if target.is_file() and target.stat().st_size > 1_000:
            return target

        total = self.plan.total_duration_seconds
        inputs: list[str] = [
            "-f", "lavfi", "-t", f"{total:.4f}",
            "-i",
            f"color=c=black@0.0:s={self.layout.width}x{self.layout.height}:"
            f"r={self.fps},format=rgba",
        ]
        next_index = 1

        def add_input(path: Path) -> int:
            nonlocal next_index
            inputs.extend(["-loop", "1", "-t", f"{total:.4f}", "-i", str(path)])
            used = next_index
            next_index += 1
            return used

        steps, current = overlay_steps(
            self._captions,
            style=self.caption_style,
            current="0:v",
            add_input=add_input,
            duration=total,
        )
        steps.append(f"[{current}]format=rgba[out]")

        partial = work / f"captions-{digest}.partial.mov"
        partial.unlink(missing_ok=True)
        args = [
            self.settings.require_tool("ffmpeg"),
            "-hide_banner", "-nostdin", "-y", *progress_args(),
            *inputs,
            "-filter_complex", ";".join(steps),
            "-map", "[out]",
            *base_output_args(fps=self.fps),
            "-t", f"{total:.4f}",
            # QT RLE keeps the alpha channel, which H.264 cannot carry.
            "-c:v", "qtrle",
            str(partial),
        ]
        await self.runner.run(
            args,
            stage="short-captions",
            expected_duration=total,
            on_progress=lambda fraction: self._emit(
                ShortPhase.BUILD_CAPTIONS, 0.4 + 0.6 * fraction, "Altyazılar çiziliyor"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )
        os.replace(partial, target)
        return target

    async def _compose(self, source: Path, output: Path) -> None:
        """Place the horizontal picture centred on the vertical canvas.

        Captions go on **after** the pad, in canvas coordinates, so they keep the
        size they were designed at instead of being scaled down with the 16:9
        picture — which is the entire point of the feature.
        """
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

        # Inputs are collected separately from output options on purpose: a bare
        # "-t" is an input option when it precedes an "-i" and an output option
        # when it follows the last one, so appending caption inputs into a list
        # that already carried the output's duration would silently retarget it.
        total = self.plan.total_duration_seconds
        single_pass = source == self.source_video
        inputs: list[str] = []
        if single_pass:
            inputs += ["-ss", f"{self.plan.groups[0].start_seconds:.6f}"]
        inputs += ["-i", str(source)]

        # Every value below is an integer this module computed, or a hex colour
        # matched against _HEX_COLOUR. No user text is interpolated.
        steps = [
            f"[0:v]scale={geometry.inner_width}:{geometry.inner_height}:flags=bicubic,"
            f"setsar=1,"
            f"pad={self.layout.width}:{self.layout.height}:"
            f"{geometry.offset_x}:{geometry.offset_y}:color={colour},"
            f"fps={self.fps},format=rgba[canvas]"
        ]
        current = "canvas"
        next_index = 1

        def add_input(path: Path) -> int:
            nonlocal next_index
            inputs.extend(["-loop", "1", "-t", f"{total:.4f}", "-i", str(path)])
            used = next_index
            next_index += 1
            return used

        if self._captions.precomposed is not None:
            inputs.extend(["-i", str(self._captions.precomposed)])
            index = next_index
            next_index += 1
            steps.append(f"[{index}:v]format=rgba[captions]")
            steps.append(f"[{current}][captions]overlay=0:0[captioned]")
            current = "captioned"
        elif self._captions.cards:
            caption_steps, current = overlay_steps(
                self._captions,
                style=self.caption_style,
                current=current,
                add_input=add_input,
                duration=total,
            )
            steps.extend(caption_steps)

        steps.append(f"[{current}]format=yuv420p[v]")

        args = [
            self.settings.require_tool("ffmpeg"),
            "-hide_banner", "-nostdin", "-y", *progress_args(),
            *inputs,
        ]
        if single_pass:
            args += ["-t", f"{self.plan.groups[0].duration_seconds:.6f}"]
        elif next_index > 1:
            # Looped caption stills never end, so a multi-cut compose needs the
            # duration stated explicitly once anything else is an input.
            args += ["-t", f"{total:.6f}"]
        args += [
            "-filter_complex", ";".join(steps),
            "-map", "[v]", "-map", "0:a:0",
            *base_output_args(fps=self.fps),
            *short_video_args(),
            *SHORT_AUDIO_ARGS,
            "-movflags", "+faststart",
            str(output),
        ]

        self._emit(ShortPhase.COMPOSE, 0.0, "Dikey görüntü hazırlanıyor")
        await self.runner.run(
            args,
            stage="short-compose",
            expected_duration=self.plan.total_duration_seconds,
            on_progress=lambda fraction: self._emit(
                ShortPhase.COMPOSE, fraction, f"Dikey görüntü hazırlanıyor — %{fraction * 100:.0f}"
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
            caption_mode=self.caption_mode,
            caption_style=(
                self.caption_style
                if self.caption_mode is ShortCaptionMode.SHORTS_NATIVE
                else None
            ),
            captions=self._provenance(),
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

    def _provenance(self) -> ShortCaptionProvenance:
        """A compact record of where this Short's captions came from."""
        package: ShortsSourcePackage | None = (
            self.manifest.shorts_source if self.clean_master is not None else None
        )
        return ShortCaptionProvenance(
            mode=self.caption_mode,
            source_cue_count=len(self.cue_sidecar.cues) if self.cue_sidecar else 0,
            rendered_cue_count=len(self._captions.cards),
            fitted_font_size=self._captions.fitted_font_size,
            font_family=self.caption_style.font_family,
            safe_bottom_inset=self.caption_style.safe_bottom_inset,
            clean_master=package.clean_master.filename if package else None,
            clean_master_sha256=package.clean_master.sha256 if package else None,
            clean_master_origin=package.origin if package else None,
            cue_sidecar=package.cue_sidecar.filename if package else None,
            cue_sidecar_sha256=package.cue_sidecar.sha256 if package else None,
            cue_content_hash=package.cue_sidecar.content_hash if package else None,
            cue_schema_version=package.cue_sidecar.schema_version if package else None,
            cue_timing_source=package.cue_sidecar.timing_source if package else None,
            precomposed=self._captions.precomposed is not None,
        )

    def _segment_path(self, group: ShortGroupPlan) -> Path:
        """Content-addressed cache path for one cut.

        Keyed on the checksum of the file the cut came out of, so a cut of the
        captioned export and the same span of the clean master can never be
        confused for each other.
        """
        name = segment_cache_name(
            self.source_sha256,
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
            "Arka plan rengi geçerli değil.",
            details=f"gelen {value!r}, beklenen #RRGGBB",
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
