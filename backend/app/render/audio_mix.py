"""Audio assembly: narration placement, music, ducking and loudness.

Narration is never concatenated. Each clip is delayed to the **absolute start
time the timeline computed** and mixed, so audio cannot drift out of sync with
the picture no matter how many transitions overlap the video above it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import ErrorCode, ValidationError
from app.models.enums import MusicSource
from app.models.project import Project
from app.render.ffmpeg import Capabilities
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join
from app.timing.schedule import Timeline

logger = logging.getLogger("evb.audio_mix")

SAMPLE_RATE = 48_000

#: Short fade applied to the head and tail of every narration clip. Prevents the
#: click that a hard cut into a non-zero sample produces.
EDGE_FADE_SECONDS = 0.015


@dataclass
class AudioPlan:
    """Everything the assembly command needs to build the audio track."""

    #: Input files, in the order they must be passed to FFmpeg.
    inputs: list[Path] = field(default_factory=list)
    #: filter_complex fragments producing the final ``[aout]`` label.
    filters: list[str] = field(default_factory=list)
    #: The label carrying the finished mix, or None when there is no audio.
    output_label: str | None = None
    music_source: MusicSource = MusicSource.NONE
    narration_count: int = 0
    notes: list[str] = field(default_factory=list)


def resolve_music_file(project: Project, paths: ProjectPaths) -> Path | None:
    """Locate the uploaded music track, if the project uses one."""
    if project.music.source is not MusicSource.UPLOADED:
        return None
    if not project.music.file:
        raise ValidationError(
            ErrorCode.MISSING_AUDIO,
            "Music is set to 'uploaded' but no file has been chosen.",
            suggestion="Upload a music file on the Audio tab, or set music to 'No music'.",
        )
    path = safe_join(paths.music, project.music.file)
    if not path.is_file():
        raise ValidationError(
            ErrorCode.MISSING_AUDIO,
            f"The music file '{project.music.file}' is missing from this project.",
            details=str(path),
            suggestion="Re-upload the music file, or switch music off.",
        )
    return path


def build_audio_plan(
    project: Project,
    timeline: Timeline,
    paths: ProjectPaths,
    *,
    capabilities: Capabilities | None = None,
    music_path: Path | None = None,
    first_input_index: int = 0,
) -> AudioPlan:
    """Build the narration + music mix for the whole video.

    ``first_input_index`` is where this plan's inputs start in the assembled
    command, since the video clips occupy the earlier indices.
    """
    plan = AudioPlan(music_source=project.music.source)
    has_sidechain = capabilities is None or capabilities.has_sidechain
    has_loudnorm = capabilities is None or capabilities.has_loudnorm

    narration_labels: list[str] = []
    index = first_input_index

    for entry in timeline.entries:
        if entry.narration_duration_seconds <= 0:
            continue
        unit = _unit_for(project, entry.unit_id)
        if unit is None or not unit.audio_file:
            continue

        audio_path = safe_join(paths.root, unit.audio_file)
        if not audio_path.is_file():
            raise ValidationError(
                ErrorCode.MISSING_AUDIO,
                f"Narration audio for '{entry.unit_id}' is missing.",
                details=str(audio_path),
                suggestion="Regenerate narration on the Audio tab, or re-upload the file.",
            )

        plan.inputs.append(audio_path)
        label = f"n{len(narration_labels)}"
        delay_ms = int(round(entry.narration_start_seconds * 1000))
        fade_out_start = max(0.0, entry.narration_duration_seconds - EDGE_FADE_SECONDS)

        plan.filters.append(
            f"[{index}:a]"
            f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
            # Tiny edge fades kill the click at each clip boundary.
            f"afade=t=in:st=0:d={EDGE_FADE_SECONDS},"
            f"afade=t=out:st={fade_out_start:.4f}:d={EDGE_FADE_SECONDS},"
            f"volume={project.audio.voice_volume_db:.2f}dB,"
            # The absolute placement that keeps audio locked to the picture.
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        narration_labels.append(label)
        index += 1

    plan.narration_count = len(narration_labels)
    total = timeline.total_duration_seconds

    if not narration_labels:
        plan.notes.append("This project has no narration audio, so the video will be silent.")
        return plan

    # --- narration bus ---------------------------------------------------
    if len(narration_labels) == 1:
        plan.filters.append(f"[{narration_labels[0]}]apad=whole_dur={total:.4f}[speech]")
    else:
        joined = "".join(f"[{label}]" for label in narration_labels)
        plan.filters.append(
            f"{joined}amix=inputs={len(narration_labels)}:normalize=0:dropout_transition=0,"
            f"apad=whole_dur={total:.4f}[speech]"
        )

    # --- music -----------------------------------------------------------
    music_label: str | None = None
    if project.music.source is not MusicSource.NONE and music_path is not None:
        plan.inputs.append(music_path)
        fade = project.audio.music_fade_seconds
        fade_out_start = max(0.0, total - fade)
        loop = "aloop=loop=-1:size=2e9," if project.music.loop_if_short else ""
        plan.filters.append(
            f"[{index}:a]"
            f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
            f"{loop}"
            f"atrim=0:{total:.4f},asetpts=PTS-STARTPTS,"
            f"volume={project.audio.music_volume_db:.2f}dB,"
            f"afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={fade_out_start:.4f}:d={fade:.3f}[musicraw]"
        )
        music_label = "musicraw"
        index += 1

    # --- ducking ---------------------------------------------------------
    if music_label is None:
        mixed = "speech"
    elif project.audio.duck_music_under_speech and has_sidechain:
        # The narration bus is duplicated: one copy is heard, the other is the
        # sidechain key that pushes the music down while anyone is talking.
        plan.filters.append("[speech]asplit=2[speechout][speechkey]")
        plan.filters.append(
            f"[{music_label}][speechkey]sidechaincompress="
            f"threshold=0.02:ratio={project.audio.duck_strength:.1f}:"
            f"attack=20:release=400:makeup=1[ducked]"
        )
        plan.filters.append("[speechout][ducked]amix=inputs=2:normalize=0:duration=longest[mixed]")
        mixed = "mixed"
    else:
        if project.audio.duck_music_under_speech and not has_sidechain:
            plan.notes.append(
                "This FFmpeg build has no 'sidechaincompress' filter, so the music sits at a "
                "fixed lower level instead of ducking dynamically."
            )
            plan.filters.append(f"[{music_label}]volume=-6dB[bedstatic]")
            music_label = "bedstatic"
        plan.filters.append(f"[speech][{music_label}]amix=inputs=2:normalize=0:duration=longest[mixed]")
        mixed = "mixed"

    # --- loudness --------------------------------------------------------
    if project.audio.normalize_loudness and has_loudnorm:
        plan.filters.append(
            f"[{mixed}]loudnorm=I={project.audio.target_lufs:.1f}:TP=-1.5:LRA=11,"
            f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
            f"alimiter=limit=0.97[aout]"
        )
    else:
        if project.audio.normalize_loudness and not has_loudnorm:
            plan.notes.append(
                "This FFmpeg build has no 'loudnorm' filter, so a fixed limiter is used "
                "instead of EBU R128 normalization."
            )
        plan.filters.append(
            f"[{mixed}]aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo,"
            f"alimiter=limit=0.97[aout]"
        )

    plan.output_label = "aout"
    return plan


def _unit_for(project: Project, unit_id: str):  # noqa: ANN202
    if unit_id == "intro":
        return project.intro
    if unit_id == "outro":
        return project.outro
    return project.scene_by_id(unit_id)


async def render_narration_only(
    project: Project,
    timeline: Timeline,
    paths: ProjectPaths,
    target: Path,
    *,
    settings: Settings | None = None,
) -> Path:
    """Export the narration mix on its own, without music.

    Useful for re-cutting the video elsewhere, or checking the voice track.
    """
    from app.render.ffmpeg import FFmpegRunner

    active = settings or get_settings()
    runner = FFmpegRunner(active)
    ffmpeg = active.require_tool("ffmpeg")

    # Build a plan with music forced off, without mutating the real project.
    narration_only = project.model_copy(deep=True)
    narration_only.music.source = MusicSource.NONE
    plan = build_audio_plan(narration_only, timeline, paths)

    if plan.output_label is None:
        raise ValidationError(
            ErrorCode.MISSING_AUDIO,
            "There is no narration to export.",
            suggestion="Generate narration on the Audio tab first.",
        )

    args = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for path in plan.inputs:
        args += ["-i", str(path)]
    args += [
        "-filter_complex", ";".join(plan.filters),
        "-map", f"[{plan.output_label}]",
        "-t", f"{timeline.total_duration_seconds:.4f}",
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
        str(target),
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    await runner.run(args, stage="narration-export")
    return target
