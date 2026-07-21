"""The timeline: the single source of truth for every absolute time.

These tests exist because transitions overlap adjacent sections. If the timeline
gets that wrong, narration drifts further out of sync with every transition —
a failure that is invisible in a 2-scene test and glaring in a 12-scene video.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import AudioSource, DurationMode, TransitionPreset
from app.models.project import Project, Scene
from app.timing.schedule import (
    MAX_TRANSITION_RATIO,
    build_timeline,
    duration_summary,
    section_duration,
)


def make_project(
    *,
    scene_audio: list[float],
    transition: float = 0.5,
    lead_in: float = 0.35,
    tail: float = 0.65,
    audio_tail: float = 2.0,
    intro_audio: float | None = None,
    outro_audio: float | None = None,
    duration_mode: DurationMode = DurationMode.AUDIO,
    target: float = 300.0,
) -> Project:
    project = Project(name="Dodo")
    project.video.transition_duration_seconds = transition
    project.video.scene_lead_in_seconds = lead_in
    project.video.scene_tail_seconds = tail
    project.video.audio_tail_seconds = audio_tail
    project.video.duration_mode = duration_mode
    project.video.target_duration_seconds = target
    project.style.transition_preset = TransitionPreset.CROSS_DISSOLVE

    project.scenes = [
        Scene(
            title=f"Scene {i + 1}",
            narration=f"Narration for scene {i + 1}. It has two sentences here.",
            audio_duration_seconds=duration,
            audio_source=AudioSource.GENERATED,
            audio_file=f"audio/generated/s{i}.mp3",
        )
        for i, duration in enumerate(scene_audio)
    ]

    if intro_audio is not None:
        project.intro.narration = "Intro narration goes here, setting up the story."
        project.intro.audio_duration_seconds = intro_audio
        project.intro.audio_source = AudioSource.GENERATED
        project.intro.audio_file = "audio/generated/intro.mp3"
    else:
        project.intro.enabled = False

    if outro_audio is not None:
        project.outro.narration = "Outro narration, thanking the viewer for watching."
        project.outro.audio_duration_seconds = outro_audio
        project.outro.audio_source = AudioSource.GENERATED
        project.outro.audio_file = "audio/generated/outro.mp3"
    else:
        project.outro.enabled = False

    return project


class TestSectionDuration:
    def test_audio_plus_padding(self) -> None:
        project = make_project(scene_audio=[10.0], lead_in=0.5, tail=1.0)
        assert section_duration(project, project.scenes[0]) == pytest.approx(11.5)

    def test_manual_override_wins(self) -> None:
        project = make_project(scene_audio=[10.0])
        project.scenes[0].manual_duration_seconds = 4.0
        assert section_duration(project, project.scenes[0]) == 4.0

    def test_scene_without_narration_gets_a_readable_hold(self) -> None:
        project = make_project(scene_audio=[0.0])
        project.scenes[0].audio_duration_seconds = None
        assert section_duration(project, project.scenes[0]) >= 1.5


class TestTransitionOverlap:
    def test_total_accounts_for_transition_overlap(self) -> None:
        """total == sum(durations) - sum(transitions) + audio tail."""
        project = make_project(scene_audio=[10.0, 10.0, 10.0], transition=0.6, audio_tail=2.0)
        timeline = build_timeline(project)

        section_total = sum(e.duration_seconds for e in timeline.entries)
        overlap = timeline.transition_total_seconds

        assert overlap == pytest.approx(1.2), "2 transitions between 3 scenes"
        assert timeline.total_duration_seconds == pytest.approx(section_total - overlap + 2.0)

    def test_each_scene_starts_a_transition_early(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0, 10.0], transition=0.6)
        entries = build_timeline(project).entries
        scene_length = entries[0].duration_seconds

        assert entries[0].start_seconds == 0.0
        assert entries[1].start_seconds == pytest.approx(scene_length - 0.6)
        assert entries[2].start_seconds == pytest.approx(2 * (scene_length - 0.6))

    def test_no_transition_means_no_overlap(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0])
        project.style.transition_preset = TransitionPreset.NONE
        timeline = build_timeline(project)

        assert timeline.transition_total_seconds == 0.0
        assert timeline.entries[1].start_seconds == pytest.approx(timeline.entries[0].duration_seconds)

    def test_final_section_has_no_outgoing_transition(self) -> None:
        timeline = build_timeline(make_project(scene_audio=[8.0, 8.0, 8.0]))
        assert timeline.entries[-1].transition is TransitionPreset.NONE
        assert timeline.entries[-1].transition_duration == 0.0


class TestNarrationDrift:
    def test_narration_does_not_drift_over_many_transitions(self) -> None:
        """The regression this whole module exists to prevent."""
        project = make_project(scene_audio=[12.0] * 12, transition=0.6, lead_in=0.4, tail=0.8)
        timeline = build_timeline(project)

        section_length = 0.4 + 12.0 + 0.8  # lead-in + narration + tail
        step = section_length - 0.6  # each section starts one transition early

        for index, entry in enumerate(timeline.entries):
            expected_start = index * step
            assert entry.start_seconds == pytest.approx(expected_start, abs=0.001), (
                f"scene {index} drifted: {entry.start_seconds} != {expected_start}"
            )
            assert entry.narration_start_seconds == pytest.approx(expected_start + 0.4, abs=0.001)

    def test_narration_begins_at_its_own_scene_start(self) -> None:
        project = make_project(scene_audio=[9.0] * 6, lead_in=0.35)
        for entry in build_timeline(project).entries:
            offset = entry.narration_start_seconds - entry.start_seconds
            assert offset == pytest.approx(0.35, abs=0.001)

    def test_narration_never_runs_past_its_own_section(self) -> None:
        project = make_project(scene_audio=[9.0] * 6)
        for entry in build_timeline(project).entries:
            assert entry.narration_end_seconds <= entry.end_seconds + 1e-6

    def test_final_narration_ends_before_the_video_does(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0, 10.0], audio_tail=2.0)
        timeline = build_timeline(project)

        assert timeline.last_narration_end < timeline.total_duration_seconds
        remaining = timeline.total_duration_seconds - timeline.last_narration_end
        assert remaining >= 2.0, "the configured audio tail must survive"

    def test_lead_in_cannot_push_narration_out_of_a_short_scene(self) -> None:
        project = make_project(scene_audio=[8.0], lead_in=2.0)
        project.scenes[0].manual_duration_seconds = 8.5  # shorter than lead-in + narration
        entry = build_timeline(project).entries[0]

        assert entry.narration_end_seconds <= entry.end_seconds + 1e-6


class TestIntroOutro:
    def test_included_in_order_and_in_the_total(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0], intro_audio=6.0, outro_audio=8.0)
        timeline = build_timeline(project)

        kinds = [e.kind for e in timeline.entries]
        assert kinds == ["intro", "scene", "scene", "outro"]

        summary = duration_summary(timeline, project)
        assert summary["introSeconds"] > 0
        assert summary["outroSeconds"] > 0
        assert summary["sceneCount"] == 2

    def test_disabled_sections_are_omitted(self) -> None:
        project = make_project(scene_audio=[10.0], intro_audio=6.0, outro_audio=8.0)
        project.intro.enabled = False
        assert [e.kind for e in build_timeline(project).entries] == ["scene", "outro"]

    def test_disabled_scenes_are_skipped(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0, 10.0])
        project.scenes[1].enabled = False
        timeline = build_timeline(project)
        assert len(timeline.scene_entries) == 2


class TestDurationModes:
    def test_audio_mode_follows_narration(self) -> None:
        project = make_project(scene_audio=[5.0, 15.0], duration_mode=DurationMode.AUDIO)
        entries = build_timeline(project).entries
        assert entries[1].duration_seconds > entries[0].duration_seconds * 2

    def test_target_mode_reaches_the_target(self) -> None:
        project = make_project(
            scene_audio=[10.0] * 5, duration_mode=DurationMode.TARGET, target=180.0, audio_tail=2.0
        )
        timeline = build_timeline(project)
        # Total is the target minus transition overlap, plus the audio tail.
        expected = 180.0 - timeline.transition_total_seconds + 2.0
        assert timeline.total_duration_seconds == pytest.approx(expected, abs=0.1)

    def test_target_mode_never_shortens_below_narration(self) -> None:
        project = make_project(
            scene_audio=[30.0] * 8, duration_mode=DurationMode.TARGET, target=60.0
        )
        timeline = build_timeline(project)

        for entry in timeline.entries:
            assert entry.duration_seconds >= entry.narration_duration_seconds
        assert any("longer than" in w for w in timeline.warnings)

    def test_target_mode_warns_when_padding_is_added(self) -> None:
        project = make_project(
            scene_audio=[10.0] * 3, duration_mode=DurationMode.TARGET, target=300.0
        )
        assert any("extra visual hold" in w for w in build_timeline(project).warnings)

    def test_manual_durations_are_honoured(self) -> None:
        project = make_project(scene_audio=[4.0, 2.0], duration_mode=DurationMode.MANUAL)
        project.scenes[0].manual_duration_seconds = 7.0
        project.scenes[1].manual_duration_seconds = 3.0
        entries = build_timeline(project).entries
        assert entries[0].duration_seconds == 7.0
        assert entries[1].duration_seconds == 3.0

    def test_manual_duration_shorter_than_narration_names_the_scene(self) -> None:
        """Narration is never cut, and the error says exactly which scene is at fault."""
        project = make_project(scene_audio=[5.0, 10.0], duration_mode=DurationMode.MANUAL)
        project.scenes[0].manual_duration_seconds = 6.0
        project.scenes[1].manual_duration_seconds = 3.0  # 10s of narration will not fit

        with pytest.raises(AppError) as exc_info:
            build_timeline(project)

        error = exc_info.value
        assert error.code is ErrorCode.INVALID_DURATION
        assert "Scene 2" in error.message
        assert "cut off" in error.message
        # The suggestion tells the user the minimum duration that would work.
        assert "10." in error.suggestion


class TestTransitionValidation:
    def test_transition_longer_than_a_scene_is_rejected(self) -> None:
        project = make_project(scene_audio=[1.0, 10.0], transition=4.0)
        project.scenes[0].manual_duration_seconds = 2.0
        with pytest.raises(AppError) as exc_info:
            build_timeline(project)
        assert exc_info.value.code is ErrorCode.INVALID_TRANSITION
        assert "shorten" in exc_info.value.suggestion.lower()

    def test_an_over_long_transition_is_clamped_with_a_warning(self) -> None:
        project = make_project(scene_audio=[3.0, 3.0], transition=3.0)
        project.scenes[0].manual_duration_seconds = 5.0
        project.scenes[1].manual_duration_seconds = 5.0

        timeline = build_timeline(project)

        assert timeline.entries[0].transition_duration == pytest.approx(5.0 * MAX_TRANSITION_RATIO)
        assert any("shortened" in w for w in timeline.warnings)

    def test_per_scene_override_is_used(self) -> None:
        project = make_project(scene_audio=[10.0, 10.0], transition=0.5)
        project.scenes[0].transition_preset = TransitionPreset.FADE_THROUGH_BLACK
        project.scenes[0].transition_duration_seconds = 1.0

        entry = build_timeline(project).entries[0]
        assert entry.transition is TransitionPreset.FADE_THROUGH_BLACK
        assert entry.transition_duration == 1.0


class TestSubtitleIntegration:
    def test_cues_land_inside_their_own_section(self) -> None:
        project = make_project(scene_audio=[12.0, 12.0, 12.0])
        timeline = build_timeline(project)

        assert len(timeline.cues) > 3
        for entry in timeline.entries:
            for cue in timeline.cues_by_unit.get(entry.unit_id, []):
                assert cue.start_seconds >= entry.narration_start_seconds - 0.01
                assert cue.end_seconds <= entry.narration_end_seconds + 0.05

    def test_cues_are_globally_ordered_and_non_overlapping(self) -> None:
        timeline = build_timeline(make_project(scene_audio=[12.0] * 6))
        for previous, current in zip(timeline.cues, timeline.cues[1:], strict=False):
            assert current.start_seconds >= previous.end_seconds

    def test_cues_are_numbered_continuously_across_scenes(self) -> None:
        timeline = build_timeline(make_project(scene_audio=[12.0] * 4))
        assert [c.index for c in timeline.cues] == list(range(1, len(timeline.cues) + 1))

    def test_scene_without_audio_contributes_no_cues(self) -> None:
        project = make_project(scene_audio=[12.0, 12.0])
        project.scenes[1].audio_duration_seconds = None
        project.scenes[1].manual_duration_seconds = 5.0
        timeline = build_timeline(project)
        assert project.scenes[1].id not in timeline.cues_by_unit


class TestValidation:
    def test_empty_project_is_rejected(self) -> None:
        project = Project(name="Empty")
        project.intro.enabled = False
        project.outro.enabled = False
        with pytest.raises(AppError) as exc_info:
            build_timeline(project)
        assert exc_info.value.code is ErrorCode.INVALID_DURATION

    def test_summary_reports_the_difference_from_target(self) -> None:
        project = make_project(scene_audio=[10.0] * 3, target=300.0)
        summary = duration_summary(build_timeline(project), project)

        assert summary["targetSeconds"] == 300.0
        assert summary["differenceSeconds"] < 0, "this project is shorter than target"
        assert ":" in str(summary["totalFormatted"])

    def test_timeline_is_deterministic(self) -> None:
        project = make_project(scene_audio=[9.0, 11.0, 7.0], intro_audio=5.0, outro_audio=6.0)
        first = build_timeline(project)
        second = build_timeline(project)
        assert [e.start_seconds for e in first.entries] == [e.start_seconds for e in second.entries]
        assert first.total_duration_seconds == second.total_duration_seconds
