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
import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, RenderError, ValidationError
from app.models.enums import (
    AudioSource,
    JobPhase,
    MusicSource,
    QualityPreset,
    TransitionPreset,
)
from app.models.project import Project, Scene, Section
from app.render import transitions
from app.render.audio_mix import build_audio_plan, render_narration_only, resolve_music_file
from app.render.clean_master import (
    CLEAN_MASTER_CACHE_SLUG,
    CleanMasterPlan,
    clean_master_cache_key,
    clean_master_path,
    clean_master_project,
    plan_clean_master,
)
from app.render.codecs import (
    AUDIO_ARGS,
    RenderProfile,
    estimate_disk_mb,
    quality_spec,
    render_profile,
)
from app.render.ffmpeg import (
    CancelledRender,
    FFmpegRunner,
    base_output_args,
    progress_args,
)
from app.render.scene_clip import SceneClip, render_scene_clip, resolve_image
from app.render.validate import ValidationReport, validate_output
from app.shorts.cues import build_sidecar, sidecar_path_for, write_sidecar
from app.shorts.manifest import (
    CLEAN_MASTER_FROM_DEDICATED_PASS,
    CLEAN_MASTER_FROM_PRIMARY_EXPORT,
    ManifestProfile,
    ShortsSourcePackage,
    describe_file,
    write_render_manifest,
)
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join, unique_path
from app.synth.music import render_ambient_bed
from app.timing.probe import measure_speech_onset
from app.timing.schedule import Timeline, build_timeline
from app.timing.subtitles import render_srt, validate_cues
from app.tts.base import WordTiming
from app.tts.narration import (
    collect_word_timings,
    generate_for_unit,
    iter_units,
    units_needing_audio,
)

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
    JobPhase.PREPARE_SHORTS_SOURCE: 0.0,
    JobPhase.WRITE_ARTIFACTS: 0.02,
    JobPhase.CLEANUP: 0.01,
}

#: Share of the bar the clean-master pass takes when it actually has to run. It
#: re-renders every scene clip and re-encodes the whole video, so it is close to
#: a second render; a smaller number would park the bar at 97% for minutes.
SHORTS_SOURCE_WEIGHT = 0.38


def phase_weights(*, with_shorts_source: bool) -> dict[JobPhase, float]:
    """Weights for one render, with room reserved for a clean-master pass.

    Every other phase is scaled down proportionally rather than re-tuned by hand,
    so a render that skips the pass is weighted exactly as it always was.
    """
    if not with_shorts_source:
        return dict(PHASE_WEIGHTS)
    scale = 1.0 - SHORTS_SOURCE_WEIGHT
    weights = {phase: weight * scale for phase, weight in PHASE_WEIGHTS.items()}
    weights[JobPhase.PREPARE_SHORTS_SOURCE] = SHORTS_SOURCE_WEIGHT
    return weights


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
    #: Versioned section timeline written beside the MP4, so a Short can be cut
    #: from this render later without guessing where a scene started.
    manifest: Path | None = None


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
            f"Diskte yeterli yer yok: bu video için yaklaşık "
            f"{estimate['totalMb'] / 1024:.1f} GB, ayrıca {margin_mb / 1024:.1f} GB güvenlik "
            f"payı gerekiyor; ama sadece {free_mb / 1024:.1f} GB boş yer var.",
            details=(
                f"sahne dosyaları  ~{estimate['intermediateMb']:.0f} MB\n"
                f"bitmiş video     ~{estimate['outputMb']:.0f} MB\n"
                f"diğer dosyalar   ~{estimate['assetsMb']:.0f} MB\n"
                f"boş yer           {free_mb:.0f} MB"
            ),
            suggestion=(
                "Diskte yer açın, Ayarlar'dan daha hafif bir ara dosya biçimi seçin "
                "(H.264 CRF 14, ProRes'ten yaklaşık 80 kat küçüktür), video kalitesini "
                "düşürün ya da geçici dosya klasörünü daha geniş bir diske taşıyın."
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
        job_id: str = "",
    ) -> None:
        self.project = project
        self.paths = paths
        self.job_id = job_id
        self.settings = settings or get_settings()
        self.runner = FFmpegRunner(self.settings)
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self.quality = quality or project.export.quality
        # Resolution/frame-rate/supersample this render actually uses. Only a
        # preview departs from the project's own video settings; everything else
        # mirrors them exactly.
        self.profile = render_profile(project.video, self.quality)
        self.log: list[str] = []
        self.warnings: list[str] = []
        self._completed_weight = 0.0
        self._last_reported = 0.0
        # Reserve bar space up front rather than mid-render: both inputs are
        # known now, and rescaling later would stall the bar at the join.
        self._weights = phase_weights(
            with_shorts_source=(
                project.export.prepare_clean_master_for_shorts and project.subtitles.burn_in
            )
        )

    # --- progress ---------------------------------------------------------

    def _emit(self, phase: JobPhase, fraction: float, message: str) -> None:
        if self.on_progress is None:
            return
        weight = self._weights.get(phase, 0.01)
        overall = min(1.0, self._completed_weight + weight * max(0.0, min(1.0, fraction)))
        # Progress is monotonic by construction. Sub-steps report their own
        # fractions and a new step's opening estimate can otherwise undercut the
        # previous step's final one, which reads as a bar jumping backwards.
        overall = max(overall, self._last_reported)
        self._last_reported = overall
        self.on_progress(phase, overall, message)

    def _finish_phase(self, phase: JobPhase) -> None:
        self._completed_weight = min(1.0, self._completed_weight + self._weights.get(phase, 0.01))

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
                "Bu FFmpeg sürümüyle video oluşturulamaz: gerekli bazı özellikler eksik.",
                details=(
                    f"missing filters: {capabilities.missing_required_filters}\n"
                    f"missing encoders: {capabilities.missing_required_encoders}"
                ),
            )
        for note in capabilities.notes():
            self._record(f"[capability] {note}")

        # 1-2. Validate the project and its source files.
        self._emit(JobPhase.VALIDATE, 0.0, "Proje kontrol ediliyor")
        self._validate_project()
        self._finish_phase(JobPhase.VALIDATE)

        self._emit(JobPhase.VERIFY_SOURCES, 0.0, "Görseller ve sesler kontrol ediliyor")
        self._verify_sources()
        self._finish_phase(JobPhase.VERIFY_SOURCES)

        # 3-4. Narration.
        word_timings = await self._ensure_narration()
        self._finish_phase(JobPhase.GENERATE_TTS)
        self._finish_phase(JobPhase.PROBE_AUDIO)

        # 5-6. Timeline and subtitles.
        self._emit(JobPhase.COMPUTE_TIMELINE, 0.0, "Sahne süreleri hesaplanıyor")
        timeline = build_timeline(
            self.project,
            word_timings=word_timings,
            speech_starts=self._measure_speech_onsets(word_timings),
        )
        self.warnings.extend(timeline.warnings)
        self._record(
            f"[timeline] total {timeline.total_duration_seconds:.3f}s, "
            f"{len(timeline.entries)} sections, "
            f"{timeline.transition_total_seconds:.3f}s of transition overlap"
        )
        self._finish_phase(JobPhase.COMPUTE_TIMELINE)

        self._emit(JobPhase.BUILD_SUBTITLES, 0.0, "Altyazılar hazırlanıyor")
        problems = validate_cues(timeline.cues)
        if problems:
            raise RenderError(
                ErrorCode.RENDER_FAILED,
                "Oluşturulan altyazılarda sorun var.",
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
        self._emit(JobPhase.VALIDATE_OUTPUT, 0.0, "Video kontrol ediliyor")
        validation = validate_output(
            output, project=self.project, timeline=timeline, settings=self.settings,
            profile=self.profile,
        )
        if not validation.passed:
            raise RenderError(
                ErrorCode.OUTPUT_VALIDATION_FAILED,
                "Video oluştu ama kontrolden geçemedi.",
                details=validation.format_failures(),
                suggestion=(
                    "Bu olmamalıydı. Tekrar deneyin; yine olursa yukarıdaki ayrıntılarda "
                    "nelerin beklendiği ve ne bulunduğu yazıyor."
                ),
            )
        self.warnings.extend(validation.warnings)
        self._finish_phase(JobPhase.VALIDATE_OUTPUT)

        # 12b. The Shorts-ready source package. Opt-in, always additive: the
        #      export validated above is already final and is never touched by
        #      anything below.
        shorts_source = await self._prepare_shorts_source(
            timeline, output, validation, capabilities
        )
        self._finish_phase(JobPhase.PREPARE_SHORTS_SOURCE)

        # 13. Side-car artifacts.
        self._emit(JobPhase.WRITE_ARTIFACTS, 0.0, "Dosyalar yazılıyor")
        artifacts = await self._write_artifacts(
            output, timeline, validation, shorts_source=shorts_source
        )
        self._finish_phase(JobPhase.WRITE_ARTIFACTS)

        # 14. Cleanup.
        self._emit(JobPhase.CLEANUP, 0.0, "Temizlik yapılıyor")
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
                "Bu projede açık hiç sahne yok.",
                suggestion="En az bir sahneyi açın ya da hazır bir metin dosyası yükleyin.",
            )
        if self.project.video.fps <= 0:
            raise ValidationError(
                ErrorCode.INVALID_DURATION, "Saniyedeki kare sayısı sıfırdan büyük olmalı."
            )

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
                f"Seslendiriliyor: {position + 1} / {len(pending)}",
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
                    f"'{unit_id}' bölümünün metni var ama sesi yok.",
                    suggestion="Seslendirme sekmesinden seslendirin ya da bir ses dosyası yükleyin.",
                )

        # Pick up timings for every section whose audio was already cached. In
        # the normal flow narration is generated on the Audio tab and the render
        # happens later, so without this almost every cue would be estimated.
        for unit_id, timings in collect_word_timings(self.project, self.paths).items():
            word_timings.setdefault(unit_id, timings)

        units = iter_units(self.project)
        measured = sum(1 for t in word_timings.values() if t)
        self._record(
            f"[tts] {measured} of {len(units)} section(s) have measured word timings; "
            "the rest fall back to estimated cue timing"
        )

        # Narration generated before word timings were stored has none on disk,
        # so those sections are timed by estimate — which reads as subtitles
        # running slightly ahead of the voice. Say so; it is one click to fix.
        estimated = [
            unit_id
            for unit_id, unit in units
            if not word_timings.get(unit_id)
            and unit.audio_source is AudioSource.GENERATED
        ]
        if estimated:
            self.warnings.append(
                f"{len(estimated)} bölümün ses kaydı, kelime zamanlaması saklanmadan önce "
                "üretilmiş. Bu yüzden altyazıları tahminle yerleştiriliyor ve sesin yarım "
                "saniye kadar önüne geçebiliyor. Seslendirme sekmesinde bir kez “Hepsini "
                "yeniden seslendir” derseniz altyazılar kelimelere tam oturur."
            )
        return word_timings

    def _measure_speech_onsets(
        self, word_timings: dict[str, list[WordTiming]]
    ) -> dict[str, float]:
        """Leading silence per section, for the ones without word timings.

        Only imported audio reaches this: generated narration carries real word
        boundaries, which are strictly better. One extra pass per such section
        buys cues that start on the first word instead of during the silence in
        front of it.
        """
        onsets: dict[str, float] = {}
        for unit_id, unit in iter_units(self.project):
            if word_timings.get(unit_id) or not unit.audio_file:
                continue
            try:
                audio = safe_join(self.paths.root, unit.audio_file)
                onset = measure_speech_onset(
                    audio,
                    duration=unit.audio_duration_seconds,
                    settings=self.settings,
                )
            except AppError as exc:  # pragma: no cover - measurement is optional
                logger.info("speech onset for %s unavailable: %s", unit_id, exc)
                continue
            if onset > 0:
                onsets[unit_id] = onset
        if onsets:
            self._record(
                f"[tts] trimmed the leading silence of {len(onsets)} clip(s) from cue timing"
            )
        return onsets

    async def _render_clips(
        self,
        timeline: Timeline,
        *,
        project: Project | None = None,
        profile: RenderProfile | None = None,
        phase: JobPhase = JobPhase.RENDER_SCENE_CLIPS,
        span: tuple[float, float] = (0.0, 1.0),
        label: str = "Sahne oluşturuluyor",
        log_prefix: str = "clip",
    ) -> list[SceneClip]:
        """Pass A: one cached clip per section.

        ``project`` and ``profile`` are overridable so the clean-master pass can
        reuse this verbatim with burn-in off and its own clip cache namespace.
        Both default to this render's own, so the normal path is unchanged.
        """
        source = project or self.project
        prof = profile or self.profile
        clips: list[SceneClip] = []
        previous_preset = None
        total = len(timeline.entries)

        for position, entry in enumerate(timeline.entries):
            self._check_cancelled()
            unit = _unit_for(source, entry.unit_id)
            if unit is None:
                continue

            def progress(fraction: float, _position: int = position) -> None:
                self._emit(
                    phase,
                    _within(span, (_position + fraction) / total),
                    f"{label} {_position + 1} / {total}",
                )

            self._emit(
                phase, _within(span, position / total), f"{label} {position + 1} / {total}"
            )
            clip = await render_scene_clip(
                source, unit, entry, self.paths,
                cues=timeline.cues_by_unit.get(entry.unit_id, []),
                previous_preset=previous_preset,
                index=position,
                settings=self.settings,
                runner=self.runner,
                cancel_event=self.cancel_event,
                on_progress=progress,
                suppress_fade_out=(position == total - 1),
                profile=prof,
            )
            clips.append(clip)
            previous_preset = unit.animation_preset
            self._record(
                f"[{log_prefix}] {entry.unit_id}: {'reused' if clip.reused else 'rendered'} "
                f"{clip.duration_seconds:.3f}s"
            )
            for line in clip.log:
                self._record(line)

        if not clips:
            raise RenderError(ErrorCode.RENDER_FAILED, "Hiçbir sahne oluşturulamadı.")
        return clips

    async def _assemble(
        self,
        timeline: Timeline,
        clips: list[SceneClip],
        output: Path,
        capabilities,  # noqa: ANN001
        *,
        mix_phase: JobPhase = JobPhase.MIX_AUDIO,
        encode_phase: JobPhase = JobPhase.ENCODE,
        span: tuple[float, float] = (0.0, 1.0),
        label: str = "Video kaydediliyor",
    ) -> None:
        """Join the clips with transitions and mux the mixed audio.

        The phase/span arguments only move where progress is reported. The
        clean-master pass calls this with the same timeline, clips of the same
        length and the same audio plan, so both outputs share one mix by
        construction rather than by a second computation.
        """
        self._check_cancelled()
        ffmpeg = self.settings.require_tool("ffmpeg")
        fps = self.profile.fps
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
                "Bu FFmpeg sürümünde yumuşak geçiş özelliği yok; sahneler sert kesmeyle "
                "birleştirildi."
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
            self._emit(mix_phase, _within(span, 0.2), "Fon müziği üretiliyor")
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

        self._emit(encode_phase, _within(span, 0.0), label)
        await self.runner.run(
            args,
            stage="assemble",
            expected_duration=total,
            on_progress=lambda fraction: self._emit(
                encode_phase, _within(span, fraction), f"{label} — %{fraction * 100:.0f}"
            ),
            log_sink=self._record,
            cancel_event=self.cancel_event,
        )

    async def _prepare_shorts_source(
        self,
        timeline: Timeline,
        export: Path,
        validation: ValidationReport,
        capabilities,  # noqa: ANN001
    ) -> ShortsSourcePackage | None:
        """Build the clean master and the cue side-car, if this render wants one.

        Never fatal. The export above is already validated and published; if
        anything here fails the render still succeeds, the manifest simply
        records no Shorts source, and the Shorts tab says so in plain words
        instead of silently producing double-captioned clips.
        """
        plan = plan_clean_master(self.project, timeline)
        if not plan.wanted:
            self._record(f"[shorts-source] {plan.reason}")
            return None

        self._emit(JobPhase.PREPARE_SHORTS_SOURCE, 0.0, "Kısa video kaynağı hazırlanıyor")
        try:
            return await self._build_shorts_source(
                plan, timeline, export, validation, capabilities
            )
        except CancelledRender:
            raise
        except Exception as exc:  # noqa: BLE001 - additive; never fails the export
            logger.warning("could not prepare the Shorts source package: %s", exc)
            self._record(f"[shorts-source] failed: {exc}")
            self.warnings.append(
                "Kısa videolar için altyazısız kopya hazırlanamadı. Bu videodan kesilecek "
                "kısa videolar, görüntüye gömülü mevcut altyazıyı kullanacak. Videonun "
                "kendisi bundan etkilenmedi."
            )
            return None

    async def _build_shorts_source(
        self,
        plan: CleanMasterPlan,
        timeline: Timeline,
        export: Path,
        validation: ValidationReport,
        capabilities,  # noqa: ANN001
    ) -> ShortsSourcePackage:
        target = clean_master_path(self.paths, export)
        target.parent.mkdir(parents=True, exist_ok=True)

        if plan.reuse_primary_export:
            # No burned-in captions to avoid, so the export *is* the clean
            # master. Hard-linked where the filesystem allows it and copied
            # otherwise: the package must keep working after the export is
            # deleted, and it must not cost a second full-size file when it can
            # avoid one.
            self._emit(JobPhase.PREPARE_SHORTS_SOURCE, 0.5, "Preparing the Shorts source")
            _link_or_copy(export, target)
            checksum = validation.checksum or ""
            origin = CLEAN_MASTER_FROM_PRIMARY_EXPORT
        else:
            clean_profile = replace(self.profile, cache_slug=CLEAN_MASTER_CACHE_SLUG)
            clean_project = clean_master_project(self.project)
            self._record(
                f"[shorts-source] {plan.reason}; clips cached under "
                f"clips/{CLEAN_MASTER_CACHE_SLUG}/, key "
                f"{clean_master_cache_key(clean_project, timeline, clean_profile.fps)}"
            )

            clips = await self._render_clips(
                timeline,
                project=clean_project,
                profile=clean_profile,
                phase=JobPhase.PREPARE_SHORTS_SOURCE,
                span=(0.0, 0.72),
                label="Kısa video kaynağı: sahne",
                log_prefix="clean-clip",
            )
            staged = target.with_suffix(".partial.mp4")
            staged.unlink(missing_ok=True)
            await self._assemble(
                timeline, clips, staged, capabilities,
                mix_phase=JobPhase.PREPARE_SHORTS_SOURCE,
                encode_phase=JobPhase.PREPARE_SHORTS_SOURCE,
                span=(0.72, 0.98),
                label="Kısa video kaynağı kaydediliyor",
            )
            # Published the same way the export is: rename only once the whole
            # file exists, so a killed render never leaves a half-written master
            # that the next Short would happily cut from.
            os.replace(staged, target)
            checksum = ""
            origin = CLEAN_MASTER_FROM_DEDICATED_PASS

        self._emit(JobPhase.PREPARE_SHORTS_SOURCE, 0.98, "Altyazı verisi kaydediliyor")
        described = describe_file(target, checksum=checksum, settings=self.settings)

        sidecar = build_sidecar(
            project_slug=self.project.slug,
            cues=timeline.cues,
            cues_by_unit=timeline.cues_by_unit,
            clean_master_sha256=described.sha256,
            total_duration_seconds=timeline.total_duration_seconds,
            render_job_id=self.job_id,
            timing_source=self._cue_timing_source(),
        )
        ref = write_sidecar(sidecar, sidecar_path_for(target))

        self._record(
            f"[shorts-source] clean master {target.name} ({origin}), "
            f"{described.width}x{described.height} @{described.avg_frame_rate}, "
            f"{len(sidecar.cues)} cue(s) in {ref.filename}"
        )
        return ShortsSourcePackage(
            clean_master=described,
            origin=origin,
            profile=ManifestProfile(
                width=self.profile.width,
                height=self.profile.height,
                fps=self.profile.fps,
                quality=self.quality.value,
            ),
            cue_sidecar=ref,
            render_job_id=self.job_id,
            project_snapshot_sha256=hashlib.sha256(
                self.project.model_dump_json().encode("utf-8")
            ).hexdigest(),
            paired_export_sha256=validation.checksum or "",
        )

    def _cue_timing_source(self) -> str:
        """Whether the cues came from measured words, estimates, or both.

        Read back off the render log rather than threaded through every call: it
        is provenance for a debugging summary, not something anything branches
        on.
        """
        line = next((entry for entry in self.log if entry.startswith("[tts] ") and
                     "measured word timings" in entry), "")
        if not line:
            return "unknown"
        if line.startswith("[tts] 0 of"):
            return "estimated"
        measured, _, rest = line[len("[tts] "):].partition(" of ")
        total = rest.split(" ", 1)[0]
        return "measured-words" if measured == total else "mixed"

    async def _write_artifacts(
        self,
        output: Path,
        timeline: Timeline,
        validation: ValidationReport,
        *,
        shorts_source: ShortsSourcePackage | None = None,
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
                self.warnings.append(f"Sadece konuşma sesi dosyası oluşturulamadı: {exc.message}")
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

        # Record the section timeline beside the export. This is what lets the
        # Shorts feature cut an exact section out of this file later instead of
        # inferring boundaries from the container duration. Failing to write it
        # must never fail an otherwise-good render.
        try:
            artifacts.manifest = write_render_manifest(
                output,
                project=self.project,
                timeline=timeline,
                profile=self.profile,
                quality=self.quality,
                checksum=validation.checksum,
                job_id=self.job_id,
                settings=self.settings,
                shorts_source=shorts_source,
            )
        except Exception as exc:  # noqa: BLE001 - side-car, never fatal
            logger.warning("could not write the render manifest: %s", exc)
            self.warnings.append(
                "Bölüm bilgileri yazılamadı; bu videodan kısa video kesilemez. Videonun "
                "kendisi bundan etkilenmedi."
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


def _within(span: tuple[float, float], fraction: float) -> float:
    """Map a 0..1 sub-step fraction into a slice of a phase's own 0..1 range."""
    low, high = span
    return low + (high - low) * max(0.0, min(1.0, fraction))


def _link_or_copy(source: Path, target: Path) -> None:
    """Hard-link ``source`` to ``target``, copying if the filesystem refuses.

    A hard link is right when the two files really are the same bytes: it costs
    no disk, and deleting the export later leaves the clean master intact because
    the data has another name. Cross-device links and filesystems without link
    support fall back to a plain copy.
    """
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    staged = target.with_suffix(".partial.mp4")
    staged.unlink(missing_ok=True)
    shutil.copy2(source, staged)
    os.replace(staged, target)


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
