"""The timeline: one immutable object holding every absolute time in the video.

**Nothing downstream recomputes timing.** Video xfade offsets, audio `adelay`
values, overlay `enable=` windows, SRT timestamps and the music envelope all
read from here. That is what stops narration drifting once transitions start
overlapping adjacent sections.

The key subtlety: a transition of duration `d` between two sections *overlaps*
them, so the total runtime is `sum(durations) - sum(transitions)`, and every
section after the first starts earlier than a naive cumulative sum would put it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.errors import ErrorCode, ValidationError
from app.models.enums import DurationMode, TransitionPreset
from app.models.project import Project, Scene, Section
from app.timing.subtitles import Cue, build_cues
from app.tts.base import WordTiming
from app.tts.narration import INTRO_ID, OUTRO_ID

logger = logging.getLogger("evb.schedule")

#: A transition may not exceed this fraction of its shorter neighbour, or the
#: shorter section is never fully visible.
MAX_TRANSITION_RATIO = 0.4

#: Floor for any section, so a one-word narration still reads as a shot.
MIN_SECTION_SECONDS = 1.5


@dataclass(frozen=True)
class TimelineEntry:
    """One section (intro, scene or outro) placed on the absolute timeline."""

    unit_id: str
    kind: str  # "intro" | "scene" | "outro"
    index: int
    #: Absolute time this section's picture begins.
    start_seconds: float
    duration_seconds: float
    #: Absolute time the narration starts and ends within this section.
    narration_start_seconds: float
    narration_end_seconds: float
    narration_duration_seconds: float
    #: Transition into the *next* section, and its duration.
    transition: TransitionPreset
    transition_duration: float

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


@dataclass(frozen=True)
class Timeline:
    entries: list[TimelineEntry]
    total_duration_seconds: float
    narration_duration_seconds: float
    transition_total_seconds: float
    audio_tail_seconds: float
    cues: list[Cue] = field(default_factory=list)
    cues_by_unit: dict[str, list[Cue]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def entry(self, unit_id: str) -> TimelineEntry | None:
        return next((e for e in self.entries if e.unit_id == unit_id), None)

    @property
    def scene_entries(self) -> list[TimelineEntry]:
        return [e for e in self.entries if e.kind == "scene"]

    @property
    def last_narration_end(self) -> float:
        ends = [e.narration_end_seconds for e in self.entries if e.narration_duration_seconds > 0]
        return max(ends) if ends else 0.0


def resolve_transition(project: Project, unit: Scene | Section) -> tuple[TransitionPreset, float]:
    preset = project.transition_for(unit)
    duration = 0.0 if preset is TransitionPreset.NONE else project.transition_duration_for(unit)
    return preset, duration


def section_duration(project: Project, unit: Scene | Section) -> float:
    """How long this section is on screen, before target-mode redistribution."""
    if unit.manual_duration_seconds is not None:
        return unit.manual_duration_seconds

    narration = unit.audio_duration_seconds or 0.0
    if narration <= 0:
        # No narration: fall back to a readable hold rather than zero.
        return max(MIN_SECTION_SECONDS, project.video.scene_lead_in_seconds + 3.0)

    total = project.video.scene_lead_in_seconds + narration + project.video.scene_tail_seconds
    return max(total, MIN_SECTION_SECONDS)


def build_timeline(
    project: Project,
    *,
    word_timings: dict[str, list[WordTiming]] | None = None,
    validate: bool = True,
) -> Timeline:
    """Compute the complete absolute timeline for ``project``."""
    units = ordered_units(project)
    if not units:
        raise ValidationError(
            ErrorCode.INVALID_DURATION,
            "This project has nothing to render.",
            suggestion="Add at least one enabled scene with narration or an image.",
        )

    warnings: list[str] = []
    durations = [section_duration(project, unit) for _, _, unit in units]

    if project.video.duration_mode is DurationMode.TARGET:
        durations, target_warnings = _fit_to_target(project, units, durations)
        warnings.extend(target_warnings)

    transitions = _resolve_transitions(project, units, durations, warnings, validate=validate)

    entries: list[TimelineEntry] = []
    cursor = 0.0
    scene_index = 0

    for position, ((unit_id, kind, unit), duration) in enumerate(zip(units, durations, strict=True)):
        preset, transition_duration = transitions[position]
        narration = unit.audio_duration_seconds or 0.0
        lead_in = project.video.scene_lead_in_seconds if narration > 0 else 0.0
        # Never let the lead-in push narration past the end of its own section.
        lead_in = min(lead_in, max(0.0, duration - narration))
        narration_start = cursor + lead_in

        entries.append(
            TimelineEntry(
                unit_id=unit_id,
                kind=kind,
                index=scene_index if kind == "scene" else -1,
                start_seconds=round(cursor, 4),
                duration_seconds=round(duration, 4),
                narration_start_seconds=round(narration_start, 4),
                narration_end_seconds=round(narration_start + narration, 4),
                narration_duration_seconds=round(narration, 4),
                transition=preset,
                transition_duration=round(transition_duration, 4),
            )
        )
        if kind == "scene":
            scene_index += 1

        # THE critical line: the next section starts a transition-duration
        # early, because the transition overlaps both sections.
        cursor += duration - transition_duration

    # The final section is not followed by a transition, so it contributes its
    # full length. Total = last section's end, plus the closing silence.
    total = entries[-1].end_seconds + project.video.audio_tail_seconds

    cues, cues_by_unit = _build_all_cues(project, entries, units, word_timings or {})

    timeline = Timeline(
        entries=entries,
        total_duration_seconds=round(total, 4),
        narration_duration_seconds=round(sum(e.narration_duration_seconds for e in entries), 4),
        transition_total_seconds=round(sum(t[1] for t in transitions[:-1]), 4),
        audio_tail_seconds=project.video.audio_tail_seconds,
        cues=cues,
        cues_by_unit=cues_by_unit,
        warnings=warnings,
    )

    if validate:
        _validate(timeline, project)
    return timeline


def ordered_units(project: Project) -> list[tuple[str, str, Scene | Section]]:
    units: list[tuple[str, str, Scene | Section]] = []
    if project.intro.enabled and (project.intro.narration.strip() or project.intro.image_file
                                  or project.intro.use_first_scene_image):
        units.append((INTRO_ID, "intro", project.intro))
    for scene in project.active_scenes:
        units.append((scene.id, "scene", scene))
    if project.outro.enabled and (project.outro.narration.strip() or project.outro.image_file):
        units.append((OUTRO_ID, "outro", project.outro))
    return units


def _fit_to_target(
    project: Project,
    units: list[tuple[str, str, Scene | Section]],
    durations: list[float],
) -> tuple[list[float], list[str]]:
    """Distribute surplus time toward the target, never cutting narration."""
    warnings: list[str] = []
    natural_total = sum(durations)
    target = project.video.target_duration_seconds

    if natural_total >= target:
        overshoot = natural_total - target
        if overshoot > 1.0:
            warnings.append(
                f"The narration alone runs {_fmt(natural_total)}, which is "
                f"{_fmt(overshoot)} longer than the {_fmt(target)} target. Narration is never "
                "cut, so the video will be longer than the target. Shorten the narration, "
                "remove a scene, or raise the speech rate."
            )
        return durations, warnings

    # Spread the surplus proportionally, so long scenes get proportionally more
    # hold time and short ones do not stretch into dead air.
    surplus = target - natural_total
    weight_total = sum(durations)
    adjusted = [d + surplus * (d / weight_total) for d in durations]
    warnings.append(
        f"Added {_fmt(surplus)} of extra visual hold across {len(durations)} sections "
        f"to reach the {_fmt(target)} target."
    )
    return adjusted, warnings


def _resolve_transitions(
    project: Project,
    units: list[tuple[str, str, Scene | Section]],
    durations: list[float],
    warnings: list[str],
    *,
    validate: bool,
) -> list[tuple[TransitionPreset, float]]:
    """Resolve and bound each transition. The final section has none."""
    resolved: list[tuple[TransitionPreset, float]] = []

    for position, (_, _, unit) in enumerate(units):
        if position == len(units) - 1:
            resolved.append((TransitionPreset.NONE, 0.0))
            continue

        preset, duration = resolve_transition(project, unit)
        if duration <= 0:
            resolved.append((TransitionPreset.NONE, 0.0))
            continue

        neighbour_min = min(durations[position], durations[position + 1])
        allowed = neighbour_min * MAX_TRANSITION_RATIO
        if duration > allowed:
            if validate and duration > neighbour_min:
                raise ValidationError(
                    ErrorCode.INVALID_TRANSITION,
                    f"A {duration:.2f}s transition is longer than the "
                    f"{neighbour_min:.2f}s section it sits beside.",
                    details=f"section index {position}, transition {preset.value}",
                    suggestion=(
                        "Shorten the transition, or lengthen the scene. A transition cannot "
                        f"exceed {MAX_TRANSITION_RATIO:.0%} of its shorter neighbour."
                    ),
                )
            warnings.append(
                f"Transition after section {position + 1} shortened from {duration:.2f}s to "
                f"{allowed:.2f}s so both scenes stay visible."
            )
            duration = allowed
        resolved.append((preset, duration))

    return resolved


def _build_all_cues(
    project: Project,
    entries: list[TimelineEntry],
    units: list[tuple[str, str, Scene | Section]],
    word_timings: dict[str, list[WordTiming]],
) -> tuple[list[Cue], dict[str, list[Cue]]]:
    """Build subtitle cues for every section, on the absolute timeline."""
    protected_extra = list(project.pronunciation.keys())
    if project.animal.scientific_name:
        protected_extra.append(project.animal.scientific_name)

    all_cues: list[Cue] = []
    by_unit: dict[str, list[Cue]] = {}

    for entry, (unit_id, _, unit) in zip(entries, units, strict=True):
        if entry.narration_duration_seconds <= 0:
            continue

        override = getattr(unit, "subtitle_override", None)
        text = " ".join(override) if override else unit.narration
        if not text.strip():
            continue

        cues = build_cues(
            text,
            total_duration=entry.narration_duration_seconds,
            style=project.style.subtitles,
            start_offset=entry.narration_start_seconds,
            protected_extra=protected_extra,
            word_timings=word_timings.get(unit_id),
            start_index=len(all_cues) + 1,
        )
        by_unit[unit_id] = cues
        all_cues.extend(cues)

    return all_cues, by_unit


def _validate(timeline: Timeline, project: Project) -> None:
    """Postconditions the rest of the pipeline is allowed to assume."""
    if timeline.total_duration_seconds <= 0:
        raise ValidationError(
            ErrorCode.INVALID_DURATION,
            "The computed video duration is zero.",
            suggestion="Add narration or set a manual duration for at least one scene.",
        )

    # Check each section individually first: naming the offending scene is far
    # more useful than reporting that "the narration" is cut off somewhere.
    for position, entry in enumerate(timeline.entries, start=1):
        if entry.narration_duration_seconds <= 0:
            continue
        if entry.narration_end_seconds > entry.end_seconds + 1e-6:
            overflow = entry.narration_end_seconds - entry.end_seconds
            name = _entry_label(entry, position)
            raise ValidationError(
                ErrorCode.INVALID_DURATION,
                f"{name} is {entry.duration_seconds:.2f}s long but its narration needs "
                f"{entry.narration_duration_seconds:.2f}s — the last {overflow:.2f}s "
                "would be cut off.",
                details=(
                    f"section starts {entry.start_seconds:.3f}s, ends {entry.end_seconds:.3f}s; "
                    f"narration {entry.narration_start_seconds:.3f}s to "
                    f"{entry.narration_end_seconds:.3f}s"
                ),
                suggestion=(
                    "Narration is never cut. Remove the manual duration for this scene so it "
                    "follows the audio, raise it to at least "
                    f"{entry.narration_duration_seconds + project.video.scene_lead_in_seconds:.2f}s, "
                    "or shorten the narration."
                ),
            )

    last_narration = timeline.last_narration_end
    if last_narration > timeline.total_duration_seconds + 1e-6:
        raise ValidationError(
            ErrorCode.INVALID_DURATION,
            "The narration would be cut off by the end of the video.",
            details=(
                f"last narration ends at {last_narration:.3f}s but the video is "
                f"{timeline.total_duration_seconds:.3f}s long"
            ),
            suggestion="Increase the audio tail, or lengthen the final scene.",
        )

    for previous, current in zip(timeline.entries, timeline.entries[1:], strict=False):
        if current.start_seconds < previous.start_seconds:
            raise ValidationError(
                ErrorCode.INVALID_DURATION,
                "Sections are not in increasing time order.",
                details=f"{previous.unit_id} at {previous.start_seconds}s, "
                        f"{current.unit_id} at {current.start_seconds}s",
            )


def _entry_label(entry: TimelineEntry, position: int) -> str:
    if entry.kind == "intro":
        return "The intro"
    if entry.kind == "outro":
        return "The outro"
    return f"Scene {entry.index + 1}"


def _fmt(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def duration_summary(timeline: Timeline, project: Project) -> dict[str, float | str]:
    """Numbers shown before rendering, so runtime is never a surprise."""
    intro = next((e for e in timeline.entries if e.kind == "intro"), None)
    outro = next((e for e in timeline.entries if e.kind == "outro"), None)
    scenes = timeline.scene_entries
    target = project.video.target_duration_seconds

    return {
        "totalSeconds": timeline.total_duration_seconds,
        "totalFormatted": _fmt(timeline.total_duration_seconds),
        "narrationSeconds": timeline.narration_duration_seconds,
        "transitionSeconds": timeline.transition_total_seconds,
        "introSeconds": intro.duration_seconds if intro else 0.0,
        "outroSeconds": outro.duration_seconds if outro else 0.0,
        "scenesSeconds": sum(e.duration_seconds for e in scenes),
        "sceneCount": len(scenes),
        "audioTailSeconds": timeline.audio_tail_seconds,
        "targetSeconds": target,
        "differenceSeconds": round(timeline.total_duration_seconds - target, 3),
        "durationMode": project.video.duration_mode.value,
    }
