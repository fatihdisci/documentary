"""The render pipeline: transitions, codecs, disk preflight, audio plan.

The full end-to-end render lives in test_render_e2e.py; these cover the pieces
in isolation, including the ones that must hold without invoking FFmpeg.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import (
    AudioSource,
    IntermediateCodec,
    MusicSource,
    QualityPreset,
    TransitionPreset,
)
from app.models.project import Project, Scene
from app.render import transitions
from app.render.audio_mix import build_audio_plan
from app.render.codecs import (
    AUDIO_ARGS,
    INTERMEDIATE_SPECS,
    QUALITY_SPECS,
    estimate_disk_mb,
    quality_spec,
)
from app.render.pipeline import DiskSpaceError, preflight_disk_space, transition_summary
from app.storage.repository import ProjectRepository
from app.timing.schedule import build_timeline
from tests.factories import make_wav_bytes


@pytest.fixture
def repository(settings) -> ProjectRepository:  # noqa: ANN001
    return ProjectRepository(settings)


@pytest.fixture
def rendered_project(repository: ProjectRepository, settings):  # noqa: ANN001, ANN201
    """A project with real audio attached, ready to plan a render from."""
    from app.tts.narration import attach_imported_audio

    project = repository.create("Dodo")
    paths = repository.paths_for(project.slug)
    project.intro.enabled = False
    project.outro.enabled = False
    project.scenes = [
        Scene(title=f"Scene {i + 1}", narration=f"Narration number {i + 1} goes here.")
        for i in range(3)
    ]
    paths.imported_audio.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(project.scenes):
        name = f"s{index}.wav"
        (paths.imported_audio / name).write_bytes(make_wav_bytes(4.0 + index))
        attach_imported_audio(scene, paths, f"audio/imported/{name}", settings=settings)
    repository.save(project)
    return project, paths


class TestTransitions:
    def test_every_preset_maps_to_something(self) -> None:
        for preset in TransitionPreset:
            spec = transitions.spec_for(preset)
            assert spec.label
            assert spec.description

    def test_only_restrained_presets_are_auto_selectable(self) -> None:
        """Slides, flashes and blurs must never be chosen automatically."""
        restrained = {s.preset for s in transitions.restrained_choices()}
        assert restrained == {
            TransitionPreset.NONE,
            TransitionPreset.CROSS_DISSOLVE,
            TransitionPreset.DOCUMENTARY_DISSOLVE,
            TransitionPreset.FADE_THROUGH_BLACK,
        }
        for showy in (
            TransitionPreset.HORIZONTAL_SLIDE,
            TransitionPreset.VERTICAL_SLIDE,
            TransitionPreset.FADE_THROUGH_WHITE,
            TransitionPreset.BLUR_DISSOLVE,
            TransitionPreset.SUBTLE_ZOOM_DISSOLVE,
        ):
            assert not transitions.is_restrained(showy)

    def test_none_produces_a_hard_cut(self) -> None:
        assert transitions.xfade_name(TransitionPreset.NONE) is None
        assert transitions.effective_duration(TransitionPreset.NONE, 1.0) == 0.0

    def test_duration_scaling(self) -> None:
        slow = transitions.effective_duration(TransitionPreset.SLOW_CINEMATIC_DISSOLVE, 0.5)
        dip = transitions.effective_duration(TransitionPreset.DIP_TO_BLACK, 0.5)
        plain = transitions.effective_duration(TransitionPreset.DOCUMENTARY_DISSOLVE, 0.5)
        assert slow > plain > dip

    def test_default_is_a_documentary_dissolve(self) -> None:
        assert transitions.xfade_name(TransitionPreset.DOCUMENTARY_DISSOLVE) == "fade"
        assert transitions.is_restrained(TransitionPreset.DOCUMENTARY_DISSOLVE)

    def test_unavailable_xfade_is_detected(self) -> None:
        assert transitions.supported_by(frozenset({"scale"})) is False
        assert transitions.supported_by(frozenset({"scale", "xfade"})) is True

    def test_project_summary_flags_unrestrained_choices(self, repository) -> None:  # noqa: ANN001
        project = repository.create("Dodo")
        project.scenes = [
            Scene(transition_preset=TransitionPreset.HORIZONTAL_SLIDE),
            Scene(),
        ]
        summary = transition_summary(project)
        assert summary[0]["restrained"] is False
        assert summary[1]["restrained"] is True


class TestCodecs:
    def test_intermediates_are_all_defined(self) -> None:
        assert set(INTERMEDIATE_SPECS) == set(IntermediateCodec)

    def test_qualities_are_all_defined(self) -> None:
        assert set(QUALITY_SPECS) == set(QualityPreset)

    def test_every_final_encode_uses_a_compatible_pixel_format(self) -> None:
        for spec in QUALITY_SPECS.values():
            assert "yuv420p" in spec.args

    def test_final_audio_is_48khz_aac(self) -> None:
        assert "aac" in AUDIO_ARGS
        assert "48000" in AUDIO_ARGS

    def test_software_encoding_is_the_default(self) -> None:
        assert "libx264" in quality_spec(QualityPreset.YOUTUBE_HQ).args
        assert "h264_videotoolbox" in quality_spec(QualityPreset.YOUTUBE_HQ, hardware=True).args

    def test_prores_is_vastly_larger_than_h264(self) -> None:
        """Measured, and the reason ProRes is not the default."""
        h264 = INTERMEDIATE_SPECS[IntermediateCodec.H264_CRF14_FAST].mb_per_minute
        prores = INTERMEDIATE_SPECS[IntermediateCodec.PRORES_LT].mb_per_minute
        assert prores > h264 * 50

    def test_higher_quality_means_bigger_files(self) -> None:
        sizes = [QUALITY_SPECS[p].mb_per_minute for p in
                 (QualityPreset.PREVIEW, QualityPreset.STANDARD,
                  QualityPreset.HIGH, QualityPreset.YOUTUBE_HQ)]
        assert sizes == sorted(sizes)


class TestDiskEstimate:
    def test_scales_with_duration(self) -> None:
        """The video components scale linearly; a fixed asset overhead does not."""
        short = estimate_disk_mb(
            duration_seconds=60, scene_count=5,
            intermediate=IntermediateCodec.H264_CRF14_FAST, quality=QualityPreset.HIGH,
        )
        long = estimate_disk_mb(
            duration_seconds=420, scene_count=5,
            intermediate=IntermediateCodec.H264_CRF14_FAST, quality=QualityPreset.HIGH,
        )
        assert long["totalMb"] > short["totalMb"]
        # Seven times the duration means seven times the video data.
        assert long["intermediateMb"] == pytest.approx(short["intermediateMb"] * 7, rel=0.01)
        assert long["outputMb"] == pytest.approx(short["outputMb"] * 7, rel=0.01)
        # The fixed overhead (text cards, normalized images) does not grow.
        assert long["assetsMb"] == short["assetsMb"]

    def test_prores_dominates_the_estimate(self) -> None:
        h264 = estimate_disk_mb(
            duration_seconds=420, scene_count=10,
            intermediate=IntermediateCodec.H264_CRF14_FAST, quality=QualityPreset.YOUTUBE_HQ,
        )
        prores = estimate_disk_mb(
            duration_seconds=420, scene_count=10,
            intermediate=IntermediateCodec.PRORES_422, quality=QualityPreset.YOUTUBE_HQ,
        )
        assert prores["totalMb"] > h264["totalMb"] * 20

    def test_a_normal_project_is_modest(self) -> None:
        """A 7-minute 1080p60 render should not need gigabytes with H.264."""
        estimate = estimate_disk_mb(
            duration_seconds=420, scene_count=12,
            intermediate=IntermediateCodec.H264_CRF14_FAST, quality=QualityPreset.YOUTUBE_HQ,
        )
        assert estimate["totalMb"] < 500


class TestDiskPreflight:
    def test_passes_when_space_is_available(self, rendered_project, settings) -> None:  # noqa: ANN001
        project, paths = rendered_project
        timeline = build_timeline(project)
        result = preflight_disk_space(project, timeline, paths, settings)
        assert result["totalMb"] > 0
        assert result["freeMb"] > 0

    def test_blocks_with_numbers_when_space_is_short(
        self, rendered_project, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        import shutil as shutil_module

        project, paths = rendered_project
        timeline = build_timeline(project)

        class TinyDisk:
            total = 100 * 1_048_576
            used = 95 * 1_048_576
            free = 5 * 1_048_576

        monkeypatch.setattr(shutil_module, "disk_usage", lambda _p: TinyDisk)

        with pytest.raises(DiskSpaceError) as exc_info:
            preflight_disk_space(project, timeline, paths, settings)

        error = exc_info.value
        assert error.code is ErrorCode.INSUFFICIENT_DISK_SPACE
        assert error.http_status == 507
        # The message must contain real numbers, not a vague complaint.
        assert "GB" in error.message
        assert "free" in error.message.lower()
        assert "scene clips" in (error.details or "")
        assert "codec" in error.suggestion


class TestAudioPlan:
    def test_places_narration_at_absolute_timeline_positions(
        self, rendered_project
    ) -> None:  # noqa: ANN001
        """The core anti-drift guarantee, checked in the generated filters."""
        project, paths = rendered_project
        timeline = build_timeline(project)
        plan = build_audio_plan(project, timeline, paths)

        assert plan.narration_count == 3
        for entry in timeline.entries:
            expected_ms = int(round(entry.narration_start_seconds * 1000))
            assert any(f"adelay={expected_ms}|{expected_ms}" in f for f in plan.filters), (
                f"no adelay at {expected_ms}ms for {entry.unit_id}"
            )

    def test_every_narration_clip_gets_edge_fades(self, rendered_project) -> None:  # noqa: ANN001
        """Prevents the click at each clip boundary."""
        project, paths = rendered_project
        timeline = build_timeline(project)
        plan = build_audio_plan(project, timeline, paths)
        narration_filters = [f for f in plan.filters if "adelay" in f]
        for entry in narration_filters:
            assert "afade=t=in" in entry
            assert "afade=t=out" in entry

    def test_no_music_means_no_music_filters(self, rendered_project) -> None:  # noqa: ANN001
        project, paths = rendered_project
        assert project.music.source is MusicSource.NONE
        plan = build_audio_plan(project, build_timeline(project), paths)
        assert not any("sidechaincompress" in f for f in plan.filters)
        assert plan.output_label == "aout"

    def test_ducking_is_applied_when_music_is_present(
        self, rendered_project, tmp_path
    ) -> None:  # noqa: ANN001
        project, paths = rendered_project
        project.music.source = MusicSource.GENERATED_AMBIENT
        project.audio.duck_music_under_speech = True

        music = tmp_path / "bed.wav"
        music.write_bytes(make_wav_bytes(30.0))
        plan = build_audio_plan(project, build_timeline(project), paths, music_path=music)

        assert any("sidechaincompress" in f for f in plan.filters)
        # The narration bus is split so one copy keys the compressor.
        assert any("asplit" in f for f in plan.filters)

    def test_ducking_falls_back_when_the_filter_is_missing(
        self, rendered_project, tmp_path
    ) -> None:  # noqa: ANN001
        from app.render.ffmpeg import Capabilities

        project, paths = rendered_project
        project.music.source = MusicSource.GENERATED_AMBIENT
        capabilities = Capabilities(
            ffmpeg_path="x", ffprobe_path="x", ffmpeg_version="", ffprobe_version="",
            configuration="", filters=frozenset({"loudnorm"}), encoders=frozenset(),
        )
        music = tmp_path / "bed.wav"
        music.write_bytes(make_wav_bytes(30.0))

        plan = build_audio_plan(
            project, build_timeline(project), paths,
            capabilities=capabilities, music_path=music,
        )
        assert not any("sidechaincompress" in f for f in plan.filters)
        assert any("fixed lower level" in note for note in plan.notes)

    def test_loudness_normalization_targets_the_configured_lufs(
        self, rendered_project
    ) -> None:  # noqa: ANN001
        project, paths = rendered_project
        project.audio.target_lufs = -14.0
        plan = build_audio_plan(project, build_timeline(project), paths)
        assert any("loudnorm=I=-14.0" in f for f in plan.filters)

    def test_a_limiter_always_guards_against_clipping(self, rendered_project) -> None:  # noqa: ANN001
        project, paths = rendered_project
        plan = build_audio_plan(project, build_timeline(project), paths)
        assert any("alimiter" in f for f in plan.filters)

    def test_missing_narration_file_is_reported(self, rendered_project) -> None:  # noqa: ANN001
        project, paths = rendered_project
        (paths.root / project.scenes[1].audio_file).unlink()
        with pytest.raises(AppError) as exc_info:
            build_audio_plan(project, build_timeline(project), paths)
        assert exc_info.value.code is ErrorCode.MISSING_AUDIO

    def test_uploaded_music_without_a_file_is_rejected(self, rendered_project) -> None:  # noqa: ANN001
        from app.render.audio_mix import resolve_music_file

        project, paths = rendered_project
        # Bypass the model validator to simulate a hand-edited project.json.
        object.__setattr__(project.music, "source", MusicSource.UPLOADED)
        project.music.__dict__["file"] = None
        with pytest.raises(AppError) as exc_info:
            resolve_music_file(project, paths)
        assert exc_info.value.code is ErrorCode.MISSING_AUDIO

    def test_silent_project_is_reported_not_crashed(self, repository) -> None:  # noqa: ANN001
        project = repository.create("Silent")
        project.intro.enabled = False
        project.outro.enabled = False
        project.scenes = [Scene(title="No audio", manual_duration_seconds=5.0)]
        paths = repository.paths_for(project.slug)

        plan = build_audio_plan(project, build_timeline(project), paths)
        assert plan.output_label is None
        assert any("silent" in note for note in plan.notes)


class TestSceneClipCaching:
    def test_key_changes_with_anything_that_affects_pixels(self, rendered_project) -> None:  # noqa: ANN001
        from app.render.scene_clip import cache_key

        project, paths = rendered_project
        timeline = build_timeline(project)
        scene = project.scenes[0]
        entry = timeline.entries[0]
        image = paths.images / "x.png"

        base = cache_key(project, scene, entry, [], image_path=image)

        scene.title = "A new title"
        assert cache_key(project, scene, entry, [], image_path=image) != base

    def test_key_is_stable_for_things_that_do_not(self, rendered_project) -> None:  # noqa: ANN001
        """Export quality and music must not invalidate a scene clip."""
        from app.render.scene_clip import cache_key

        project, paths = rendered_project
        timeline = build_timeline(project)
        scene, entry = project.scenes[0], timeline.entries[0]
        image = paths.images / "x.png"

        base = cache_key(project, scene, entry, [], image_path=image)

        project.export.quality = QualityPreset.PREVIEW
        project.music.source = MusicSource.GENERATED_AMBIENT
        project.audio.music_volume_db = -12.0
        project.metadata.description = "totally different"

        assert cache_key(project, scene, entry, [], image_path=image) == base

    def test_burn_in_toggle_changes_the_key(self, rendered_project) -> None:  # noqa: ANN001
        from app.render.scene_clip import cache_key
        from app.timing.subtitles import Cue

        project, paths = rendered_project
        timeline = build_timeline(project)
        scene, entry = project.scenes[0], timeline.entries[0]
        image = paths.images / "x.png"
        cues = [Cue(index=1, start_seconds=0.0, end_seconds=2.0, lines=["Hello"])]

        project.subtitles.burn_in = False
        without = cache_key(project, scene, entry, cues, image_path=image)
        project.subtitles.burn_in = True
        with_burn = cache_key(project, scene, entry, cues, image_path=image)

        assert without != with_burn
