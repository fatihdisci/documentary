"""The render pipeline.

Stages, in order:

  1. validate the project          8. render scene clips (cached, Pass A)
  2. verify source files           9. assemble with transitions (Pass B)
  3. generate missing narration   10. mix audio on the same timeline
  4. probe audio durations        11. encode the final file
  5. compute the timeline         12. validate the output with ffprobe
  6. build subtitle cues          13. write artifacts
  7. preflight disk space         14. clean up

Everything timing-related comes from the Timeline built in stage 5. No stage
after it recomputes a duration or an offset.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, RenderError, ValidationError
from app.models.enums import JobPhase, MusicSource, QualityPreset, TransitionPreset
from app.models.project import Project, Scene, Section
from app.render import transitions
from app.render.audio_mix import build_audio_plan, render_narration_only, resolve_music_file
from app.render.codecs import AUDIO_ARGS, estimate_disk_mb, quality_spec
from app.render.ffmpeg import (
    CancelledRender,
    FFmpegRunner,
    base_output_args,
    progress_args,
)
from app.render.scene_clip import SceneClip, render_scene_clip, resolve_image
from app.render.validate import ValidationReport, validate_output
from app.storage.layout import ProjectPaths
from app.storage.paths import unique_path
from app.synth.music import render_ambient_bed
from app.timing.schedule import Timeline, build_timeline
from app.timing.subtitles import render_srt, validate_cues
from app.tts.base import WordTiming
from app.tts.narration import generate_for_unit, iter_units, units_needing_audio

logger = logging.getLogger("evb.pipeline")

ProgressCallback = Callable[[JobPhase, float, str], None]

#: Relative cost of each phase, used to turn per-phase progress into an overall
#: percentage that does not stall or jump.
PHASE_WEIGHTS: dict[JobPhase, float] = {
    JobPhase.VALIDATE: 0.01,
    JobPhase.VERIFY_SOURCES: 0.01,
    JobPhase.GENERATE_TTS: 0.10,
    JobPhase.PROBE_AUDIO: 0.01,
    JobPhase.COMPUTE_TIMELINE: 0.01,
    JobPhase.BUILD_SUBTITLES: 0.01,
    JobPhase.NORMALIZE_IMAGES: 0.03,
    JobPhase.RENDER_TEXT_CARDS: 0.02,
    JobPhase.RENDER_SCENE_CLIPS: 0.45,
    JobPhase.ASSEMBLE: 0.05,
    JobPhase.MIX_AUDIO: 0.02,
    JobPhase.ENCODE: 0.22,
    JobPhase.VALIDATE_OUTPUT: 0.03,
    JobPhase.WRITE_ARTIFACTS: 0.02,
    JobPhase.CLEANUP: 0.01,
}


@dataclass
class RenderArtifacts:
    video: Path
    subtitles: Path | None = None
    scene_subtitles: list[Path] = field(default_factory=list)
    narration_audio: Path | None = None
    description: Path | None = None
    thumbnail_prompt: Path | None = None
    project_snapshot: Path | None = None
    render_log: Path | None = None
    report: Path | None = None


@dataclass
class RenderResult:
    artifacts: RenderArtifacts
    timeline: Timeline
    validation: ValidationReport
    scene_clips: list[SceneClip]
    warnings: list[str] = field(default_factory=list)
    reused_clips: int = 0
    rendered_clips: int = 0


class DiskSpaceError(AppError):
    http_status = 507


def preflight_disk_space(
    project: Project,
    timeline: Timeline,
    paths: ProjectPaths,
    settings: Settings,
) -> dict[str, float]:
    """Block the render before it starts if the disk cannot hold it."""
    estimate = estimate_disk_mb(
        duration_seconds=timeline.total_duration_seconds,
        scene_count=len(timeline.entries),
        intermediate=project.export.intermediate_codec,
        quality=project.export.quality,
        hardware=project.export.use_hardware_encoder,
    )
    margin_mb = float(settings.mutable.disk_safety_margin_mb)
    required_mb = estimate["totalMb"] + margin_mb

    try:
        free_mb = shutil.disk_usage(paths.root).free / 1_048_576
    except OSError as exc:  # pragma: no cover - only on an unreadable mount
        logger.warning("could not measure free disk space: %s", exc)
        return {**estimate, "freeMb": -1.0, "requiredMb": required_mb}

    if free_mb < required_mb:
        raise DiskSpaceError(
            ErrorCode.INSUFFICIENT_DISK_SPACE,
            f"Not enough disk space to render: this needs about "
            f"{estimate['totalMb'] / 1024:.1f} GB plus a {margin_mb / 1024:.1f} GB safety "
            f"margin, but only {free_mb / 1024:.1f} GB is free.",
            details=(
                f"scene clips  ~{estimate['intermediateMb']:.0f} MB\n"
                f"final output ~{estimate['outputMb']:.0f} MB\n"
                f"other assets ~{estimate['assetsMb']:.0f} MB\n"
                f"free space    {free_mb:.0f} MB"
            ),
            suggestion=(
                "Free up disk space, choose a lighter intermediate codec in Settings "
                "(H.264 CRF 14 is ~80x smaller than ProRes), lower the export quality, "
                "or point the temporary directory at a larger volume."
            ),
        )
    return {**estimate, "freeMb": round(free_mb, 1), "requiredMb": round(required_mb, 1)}


class RenderPipeline:
    """Runs one render. Create a new instance per job."""

    def __init__(
        self,
        project: Project,
        paths: ProjectPaths,
        *,
        settings: Settings | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        quality: QualityPreset | None = None,
    ) -> None:
        self.project = project
        self.paths = paths
        self.settings = settings or get_settings()
        self.runner = FFmpegRunner(self.settings)
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self.quality = quality or project.export.quality
        self.log: list[str] = []
        self.warnings: list[str] = []
        self._completed_weight = 0.0
        self._last_reported = 0.0

    # --- progress ---------------------------------------------------------

    def _emit(self, phase: JobPhase, fraction: float, message: str) -> None:
        if self.on_progress is None:
            return
        weight = PHASE_WEIGHTS.get(phase, 0.01)
        overall = min(1.0, self._completed_weight + weight * max(0.0, min(1.0, fraction)))
        # Progress is monotonic by construction. Sub-steps report their own
        # fractions and a new step's opening estimate can otherwise undercut the
        # previous step's final one, which reads as a bar jumping backwards.
        overall = max(overall, self._last_reported)
        self._last_reported = overall
        self.on_progress(phase, overall, message)

    def _finish_phase(self, phase: JobPhase) -> None:
        self._completed_weight = min(1.0, self._completed_weight + PHASE_WEIGHTS.get(phase, 0.01))

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CancelledRender("render cancelled")

    def _record(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > 20_000:
            del self.log[:5_000]

    # --- the run ----------------------------------------------------------

    async def run(self) -> RenderResult:
        capabilities = self.runner.probe_capabilities()
        if not capabilities.is_usable:
            raise RenderError(
                ErrorCode.FFMPEG_CAPABILITY_MISSING,
                "This FFmpeg build cannot render: required components are missing.",
                details=(
                    f"missing filters: {capabilities.missing_required_filters}\n"
                    f"missing encoders: {capabilities.missing_required_encoders}"
                ),
            )
        for note in capabilities.notes():
            self._record(f"[capability] {note}")

        # 1-2. Validate the project and its source files.
        self._emit(JobPhase.VALIDATE, 0.0, "Checking the project")
        self._validate_project()
        self._finish_phase(JobPhase.VALIDATE)

        self._emit(JobPhase.VERIFY_SOURCES, 0.0, "Verifying images and audio")
        self._verify_sources()
        self._finish_phase(JobPhase.VERIFY_SOURCES)

        # 3-4. Narration.
        word_timings = await self._ensure_narration()
        self._finish_phase(JobPhase.GENERATE_TTS)
        self._finish_phase(JobPhase.PROBE_AUDIO)

        # 5-6. Timeline and subtitles.
        self._emit(JobPhase.COMPUTE_TIMELINE, 0.0, "Computing the timeline")
        timeline = build_timeline(self.project, word_timings=word_timings)
        self.warnings.extend(timeline.warnings)
        self._record(
            f"[timeline] total {timeline.total_duration_seconds:.3f}s, "
            f"{len(timeline.entries)} sections, "
            f"{timeline.transition_total_seconds:.3f}s of transition overlap"
        )
        self._finish_phase(JobPhase.COMPUTE_TIMELINE)

        self._emit(JobPhase.BUILD_SUBTITLES, 0.0, "Building subtitles")
        problems = validate_cues(timeline.cues)
        if problems:
            raise RenderError(
                ErrorCode.RENDER_FAILED,
                "The generated subtitles are not valid.",
                details="\n".join(problems[:20]),
            )
        self._record(f"[subtitles] {len(timeline.cues)} cues")
        self._finish_phase(JobPhase.BUILD_SUBTITLES)

        # 7. Disk preflight.
        disk = preflight_disk_space(self.project, timeline, self.paths, self.settings)
        self._record(
            f"[disk] need ~{disk['totalMb']:.0f} MB, free {disk.get('freeMb', -1):.0f} MB"
        )

        # 8. Scene clips.
        clips = await self._render_clips(timeline)
        self._finish_phase(JobPhase.NORMALIZE_IMAGES)
        self._finish_phase(JobPhase.RENDER_TEXT_CARDS)
        self._finish_phase(JobPhase.RENDER_SCENE_CLIPS)

        # 9-11. Assemble, mix and encode.
        output = self._next_output_path()
        await self._assemble(timeline, clips, output, capabilities)
        self._finish_phase(JobPhase.ASSEMBLE)
        self._finish_phase(JobPhase.MIX_AUDIO)
        self._finish_phase(JobPhase.ENCODE)

        # 12. Validate what was actually produced.
        self._emit(JobPhase.VALIDATE_OUTPUT, 0.0, "Validating the exported file")
        validation = validate_output(
            output, project=self.project, timeline=timeline, settings=self.settings
        )
        if not validation.passed:
            raise RenderError(
                ErrorCode.OUTPUT_VALIDATION_FAILED,
                "FFmpeg finished, but the exported file failed validation.",
                details=validation.format_failures(),
                suggestion=(
                    "This should not happen. Retry the render; if it repeats, the details "
                    "above list every assertion and the value actually found."
                ),
            )
        self.warnings.extend(validation.warnings)
        self._finish_phase(JobPhase.VALIDATE_OUTPUT)

        # 13. Side-car artifacts.
        self._emit(JobPhase.WRITE_ARTIFACTS, 0.0, "Writing exports")
        artifacts = await self._write_artifacts(output, timeline, validation)
        self._finish_phase(JobPhase.WRITE_ARTIFACTS)

        # 14. Cleanup.
        self._emit(JobPhase.CLEANUP, 0.0, "Cleaning up")
        self._cleanup()
        self._finish_phase(JobPhase.CLEANUP)

        return RenderResult(
            artifacts=artifacts,
            timeline=timeline,
            validation=validation,
            scene_clips=clips,
            warnings=self.warnings,
            reused_clips=sum(1 for c in clips if c.reused),
            rendered_clips=sum(1 for c in clips if not c.reused),
        )

    # --- stages -----------------------------------------------------------

    def _validate_project(self) -> None:
        if not self.project.active_scenes:
            raise ValidationError(
                ErrorCode.INVALID_DURATION,
                "This project has no enabled scenes.",
                suggestion="Enable at least one scene, or import a content package.",
            )
        if self.project.video.fps <= 0:
            raise ValidationError(ErrorCode.INVALID_DURATION, "The frame rate must be positive.")

    def _verify_sources(self) -> None:
        for unit_id, unit in _all_units(self.project):
            if isinstance(unit, Section) and not unit.enabled:
                continue
            resolve_image(self.project, unit, self.paths)
            del unit_id

    async def _ensure_narration(self) -> dict[str, list[WordTiming]]:
        pending = units_needing_audio(self.project)
        word_timings: dict[str, list[WordTiming]] = {}

        if pending:
            self._record(f"[tts] {len(pending)} section(s) need narration")
        for position, (unit_id, unit) in enumerate(pending):
            self._check_cancelled()
            self._emit(
                JobPhase.GENERATE_TTS,
                position / max(1, len(pending)),
                f"Generating narration {position + 1} of {len(pending)}",
            )
            outcome = await generate_for_unit(self.project, unit, unit_id, self.paths,
                                              settings=self.settings)
            if outcome.word_timings:
                word_timings[unit_id] = outcome.word_timings

        # Everything else must already have measured audio.
        for unit_id, unit in iter_units(self.project):
            if unit.narration.strip() and not unit.audio_file:
                raise ValidationError(
                    ErrorCode.MISSING_AUDIO,
                    f"Section '{unit_id}' has narration but no audio.",
                    suggestion="Generate narration on the Audio tab, or upload an audio file.",
                )
        return word_timings

    async def _render_clips(self, timeline: Timeline) -> list[SceneClip]:
        clips: list[SceneClip] = []
        previous_preset = None
        total = len(timeline.entries)

        for position, entry in enumerate(timeline.entries):
            self._check_cancelled()
            unit = _unit_for(self.project, entry.unit_id)
            if unit is None:
                continue

            def progress(fraction: float, _position: int = position) -> None:
                self._emit(
                    JobPhase.RENDER_SCENE_CLIPS,
                    (_position + fraction) / total,
                    f"Rendering scene {_position + 1} of {total}",
                )

            self._emit(
                JobPhase.RENDER_SCENE_CLIPS, position / total,
                f"Rendering scene {position + 1} of {total}",
            )
            clip = await render_scene_clip(
                self.project, unit, entry, self.paths,
                cues=timeline.cues_by_unit.get(entry.unit_id, []),
                previous_preset=previous_preset,
                index=position,
                settings=self.settings,
                runner=self.runner,
                cancel_event=self.cancel_event,
                on_progress=progress,
                suppress_fade_out=(position == total - 1),
            )
            clips.append(clip)
            previous_preset = unit.animation_preset
            self._record(
                f"[clip] {entry.unit_id}: {'reused' if clip.reused else 'rendered'} "
                f"{clip.duration_seconds:.3f}s"
            )
            for line in clip.log:
                self._record(line)

        if not clips:
            raise RenderError(ErrorCode.RENDER_FAILED, "No scene clips were produced.")
        return clips

    async def _assemble(
        self,
        timeline: Timeline,
        clips: list[SceneClip],
        output: Path,
        capabilities,  # noqa: ANN001
    ) -> None:
        """Join the clips with transitions and mux the mixed audio."""
        self._check_cancelled()
        ffmpeg = self.settings.require_tool("ffmpeg")
        fps = self.project.video.fps
        total = timeline.total_duration_seconds

        args: list[str] = [ffmpeg, "-hide_banner", "-nostdin", "-y", *progress_args()]
        for clip in clips:
            args += ["-i", str(clip.path)]

        steps: list[str] = []
        current = "0:v"

        if len(clips) > 1 and capabilities.has_xfade:
            # Each xfade offset is the *absolute* time the transition starts,
            # taken straight from the timeline. Chaining pairwise means the
            # accumulated result already carries the earlier overlaps.
            for position in range(1, len(clips)):
                entry = timeline.entries[position - 1]
                preset = entry.transition
                duration = transitions.effective_duration(preset, entry.transition_duration)
                name = transitions.xfade_name(preset)

                if name is None or duration <= 0:
                    steps.append(f"[{current}][{position}:v]concat=n=2:v=1:a=0[j{position}]")
                else:
                    offset = entry.end_seconds - duration
                    steps.append(
                        f"[{current}][{position}:v]"
                        f"xfade=transition={name}:duration={duration:.4f}:offset={offset:.4f}"
                        f"[j{position}]"
                    )
                current = f"j{position}"
        elif len(clips) > 1:
            self.warnings.append(
                "This FFmpeg build has no 'xfade' filter, so scenes are joined with hard cuts."
            )
            joined = "".join(f"[{i}:v]" for i in range(len(clips)))
            steps.append(f"{joined}concat=n={len(clips)}:v=1:a=0[joined]")
            current = "joined"

        # Hold the final frame through the closing silence.
        tail = self.project.video.audio_tail_seconds
        if tail > 0:
            steps.append(f"[{current}]tpad=stop_mode=clone:stop_duration={tail:.4f}[padded]")
            current = "padded"

            # Only fade here if the last section did not already fade to black.
            # Doing both leaves seconds of dead black at the end of the video.
            last_unit = _unit_for(self.project, timeline.entries[-1].unit_id)
            configured = (
                last_unit.fade_to_black_seconds
                if isinstance(last_unit, Section) and last_unit.fade_to_black_seconds > 0
                else min(tail, 1.2)
            )
            fade_length = min(configured, total)
            fade_start = max(0.0, total - fade_length)
            steps.append(
                f"[{current}]fade=t=out:st={fade_start:.4f}:d={fade_length:.4f}[faded]"
            )
            current = "faded"
            self._record(f"[assemble] final fade-out {fade_length:.2f}s at {fade_start:.2f}s")

        steps.append(f"[{current}]format=yuv420p,fps={fps}[v]")

        # --- audio ---------------------------------------------------------
        music_path: Path | None = None
        if self.project.music.source is MusicSource.UPLOADED:
            music_path = resolve_music_file(self.project, self.paths)
        elif self.project.music.source is MusicSource.GENERATED_AMBIENT:
            self._emit(JobPhase.MIX_AUDIO, 0.2, "Generating the ambient bed")
            music_path = await render_ambient_bed(
                total + 1.0,
                self.paths.root / "derived" / "ambient-bed.wav",
                settings=self.settings, runner=self.runner,
            )

        plan = build_audio_plan(
            self.project, timeline, self.paths,
            capabilities=capabilities, music_path=music_path,
            first_input_index=len(clips),
        )
        self.warnings.extend(plan.notes)
        for path in plan.inputs:
            args += ["-i", str(path)]
        steps.extend(plan.filters)

        spec = quality_spec(self.quality, hardware=self.project.export.use_hardware_encoder)
        args += [
            "-filter_complex", ";".join(steps),
            "-map", "[v]",
        ]
        if plan.output_label:
            args += ["-map", f"[{plan.output_label}]"]
        args += [
            *base_output_args(fps=fps),
            "-t", f"{total:.4f}",
            *spec.args,
            *(AUDIO_ARGS if plan.output_label else ["-an"]),
            "-movflags", "+faststart",
            str(output),
        ]

        self._emit(JobPhase.ENCODE, 0.0, "Encoding the final video")
        await self.runner.run(
            args,
            stage="assemble",
            expected_duration=total,
            on_progress=lambda fraction: self._emit(
                JobPhase.ENCODE, fraction, f"Encoding — {fraction * 100:.0f}%"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )

    async def _write_artifacts(
        self, output: Path, timeline: Timeline, validation: ValidationReport
    ) -> RenderArtifacts:
        import json

        artifacts = RenderArtifacts(video=output)
        stem = output.stem
        directory = output.parent

        if self.project.subtitles.export_srt and timeline.cues:
            artifacts.subtitles = directory / f"{stem}.srt"
            artifacts.subtitles.write_text(render_srt(timeline.cues), "utf-8")

        if self.project.subtitles.export_scene_srt:
            scene_dir = directory / f"{stem}-scenes"
            scene_dir.mkdir(parents=True, exist_ok=True)
            for position, entry in enumerate(timeline.entries):
                cues = timeline.cues_by_unit.get(entry.unit_id)
                if not cues:
                    continue
                name = f"{position + 1:02d}-{entry.kind}.srt"
                path = scene_dir / name
                path.write_text(render_srt(cues), "utf-8")
                artifacts.scene_subtitles.append(path)

        if self.project.export.export_narration_audio and timeline.cues:
            artifacts.narration_audio = directory / f"{stem}-narration.wav"
            try:
                await render_narration_only(
                    self.project, timeline, self.paths, artifacts.narration_audio,
                    settings=self.settings,
                )
            except AppError as exc:
                self.warnings.append(f"Narration-only export skipped: {exc.message}")
                artifacts.narration_audio = None

        if self.project.export.export_description:
            artifacts.description = directory / f"{stem}-description.txt"
            artifacts.description.write_text(
                f"{self.project.metadata.video_title}\n\n{self.project.metadata.description}\n",
                "utf-8",
            )
            artifacts.thumbnail_prompt = directory / f"{stem}-thumbnail.txt"
            artifacts.thumbnail_prompt.write_text(
                f"Thumbnail text: {self.project.metadata.thumbnail_text}\n\n"
                f"Prompt:\n{self.project.metadata.thumbnail_prompt}\n",
                "utf-8",
            )

        artifacts.project_snapshot = directory / f"{stem}-project.json"
        artifacts.project_snapshot.write_text(self.project.model_dump_json(indent=2), "utf-8")

        artifacts.render_log = directory / f"{stem}-render.log"
        artifacts.render_log.write_text("\n".join(self.log), "utf-8")

        artifacts.report = directory / f"{stem}-report.json"
        artifacts.report.write_text(
            json.dumps(
                {
                    "project": self.project.slug,
                    "output": output.name,
                    "checksum": validation.checksum,
                    "validation": validation.to_dict(),
                    "timeline": {
                        "totalSeconds": timeline.total_duration_seconds,
                        "narrationSeconds": timeline.narration_duration_seconds,
                        "transitionSeconds": timeline.transition_total_seconds,
                        "sections": [
                            {
                                "unitId": e.unit_id,
                                "kind": e.kind,
                                "startSeconds": e.start_seconds,
                                "durationSeconds": e.duration_seconds,
                                "transition": e.transition.value,
                            }
                            for e in timeline.entries
                        ],
                    },
                    "warnings": self.warnings,
                },
                indent=2,
            ),
            "utf-8",
        )
        return artifacts

    def _cleanup(self) -> None:
        if self.project.export.keep_temp_files:
            return
        if not self.settings.mutable.cleanup_temp_on_success:
            return
        # Scene clips are the cache that makes re-renders fast, so they stay.
        # Only genuinely transient files go.
        for stale in self.paths.subtitle_assets.glob("*.mov"):
            stale.unlink(missing_ok=True)

    def _next_output_path(self) -> Path:
        directory = self.paths.exports
        stem = self.project.slug
        # Auto-versioning: a re-render never overwrites a previous export.
        return unique_path(directory, stem, ".mp4")


def _all_units(project: Project) -> list[tuple[str, Scene | Section]]:
    units: list[tuple[str, Scene | Section]] = []
    if project.intro.enabled:
        units.append(("intro", project.intro))
    units.extend((scene.id, scene) for scene in project.active_scenes)
    if project.outro.enabled:
        units.append(("outro", project.outro))
    return units


def _unit_for(project: Project, unit_id: str) -> Scene | Section | None:
    if unit_id == "intro":
        return project.intro
    if unit_id == "outro":
        return project.outro
    return project.scene_by_id(unit_id)


def transition_summary(project: Project) -> list[dict[str, object]]:
    """What transitions the project will actually use, for the UI."""
    summary: list[dict[str, object]] = []
    for scene in project.active_scenes:
        preset = project.transition_for(scene)
        spec = transitions.spec_for(preset)
        summary.append(
            {
                "sceneId": scene.id,
                "preset": preset.value,
                "label": spec.label,
                "restrained": spec.restrained,
                "durationSeconds": transitions.effective_duration(
                    preset, project.transition_duration_for(scene)
                ),
            }
        )
    return summary
