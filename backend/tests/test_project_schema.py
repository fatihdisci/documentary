"""Project schema validation, serialization and migration."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.errors import AppError, ErrorCode
from app.models.enums import (
    AnimationPreset,
    DurationMode,
    MusicSource,
    TransitionPreset,
    TTSProviderName,
)
from app.models.migrations import MIGRATIONS, migrate
from app.models.project import SCHEMA_VERSION, Project, Scene


def make_project(**kwargs: object) -> Project:
    return Project(name="Dodo", **kwargs)  # type: ignore[arg-type]


class TestDefaults:
    def test_youtube_safe_defaults(self) -> None:
        p = make_project()
        assert (p.video.width, p.video.height) == (1920, 1080)
        assert p.video.fps == 60
        assert p.audio.target_lufs == -16.0
        assert 0.4 <= p.video.transition_duration_seconds <= 0.6
        assert 1.5 <= p.video.audio_tail_seconds <= 2.5
        assert p.style.transition_preset is TransitionPreset.DOCUMENTARY_DISSOLVE

    def test_subtitles_export_srt_and_burn_in_on_by_default(self) -> None:
        p = make_project()
        assert p.subtitles.export_srt is True
        # Burned in by default so a finished video is captioned out of the box;
        # the .srt sidecar is still exported alongside it.
        assert p.subtitles.burn_in is True

    def test_music_is_never_implicit(self) -> None:
        assert make_project().music.source is MusicSource.NONE

    def test_ducking_enabled_by_default(self) -> None:
        assert make_project().audio.duck_music_under_speech is True

    def test_tts_defaults(self) -> None:
        p = make_project()
        assert p.audio.tts_provider is TTSProviderName.EDGE
        assert p.audio.voice == "en-US-AndrewNeural"
        assert p.audio.speech_rate == 0.95
        assert p.video.duration_mode is DurationMode.AUDIO


class TestValidation:
    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(PydanticValidationError):
            Project.model_validate({"name": "x", "nonsenseField": 1})

    def test_rejects_odd_dimensions(self) -> None:
        # H.264 with yuv420p cannot encode odd dimensions.
        with pytest.raises(PydanticValidationError):
            make_project(video={"width": 1921, "height": 1080})

    def test_rejects_out_of_range_focus(self) -> None:
        with pytest.raises(PydanticValidationError):
            Scene(focus_x=1.5)

    def test_rejects_excessive_zoom(self) -> None:
        with pytest.raises(PydanticValidationError):
            Scene(end_scale=9.0)

    def test_pan_at_scale_one_is_rejected(self) -> None:
        # Panning with no zoom headroom would walk the frame off the image and
        # expose black borders.
        with pytest.raises(PydanticValidationError, match="black borders"):
            Scene(start_scale=1.0, end_scale=1.0, start_x=0.2, end_x=0.8)

    def test_pan_with_zoom_headroom_is_allowed(self) -> None:
        scene = Scene(start_scale=1.2, end_scale=1.2, start_x=0.2, end_x=0.8)
        assert scene.start_x == 0.2

    def test_uploaded_music_requires_a_file(self) -> None:
        with pytest.raises(PydanticValidationError, match="music.file is required"):
            make_project(music={"source": "uploaded"})

    def test_subtitle_cue_bounds_must_be_ordered(self) -> None:
        with pytest.raises(PydanticValidationError, match="minCueSeconds"):
            make_project(style={"subtitles": {"minCueSeconds": 7.0, "maxCueSeconds": 6.0}})

    def test_rotation_must_be_quadrant(self) -> None:
        with pytest.raises(PydanticValidationError):
            Scene(rotation=45)
        assert Scene(rotation=450).rotation == 90


class TestSceneOrdering:
    def test_order_is_renumbered_contiguously(self) -> None:
        p = make_project(scenes=[Scene(order=7), Scene(order=3), Scene(order=99)])
        assert [s.order for s in p.scenes] == [0, 1, 2]

    def test_active_scenes_excludes_disabled(self) -> None:
        p = make_project(scenes=[Scene(), Scene(enabled=False), Scene()])
        assert len(p.active_scenes) == 2

    def test_scene_lookup_by_id(self) -> None:
        scene = Scene()
        p = make_project(scenes=[Scene(), scene])
        assert p.scene_by_id(scene.id) is scene
        assert p.scene_by_id("missing") is None


class TestSerialization:
    def test_round_trip_is_lossless(self) -> None:
        original = make_project(
            scenes=[Scene(title="Habitat", narration="The dodo lived on Mauritius.")],
            pronunciation={"Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"},
        )
        payload = json.loads(original.model_dump_json())
        restored = Project.model_validate(payload)
        assert restored.model_dump() == original.model_dump()

    def test_wire_format_is_camel_case(self) -> None:
        payload = json.loads(make_project(scenes=[Scene()]).model_dump_json())
        assert "schemaVersion" in payload
        assert "targetDurationSeconds" in payload["video"]
        assert "animationPreset" in payload["scenes"][0]
        assert "schema_version" not in payload

    def test_accepts_snake_case_input_too(self) -> None:
        p = Project.model_validate({"name": "x", "video": {"target_duration_seconds": 240.0}})
        assert p.video.target_duration_seconds == 240.0

    def test_enums_serialize_as_their_string_values(self) -> None:
        payload = json.loads(make_project(scenes=[Scene()]).model_dump_json())
        assert payload["scenes"][0]["animationPreset"] == AnimationPreset.AUTO.value == "auto"


class TestTransitionResolution:
    def test_scene_inherits_project_default(self) -> None:
        p = make_project(scenes=[Scene()])
        assert p.transition_for(p.scenes[0]) is TransitionPreset.DOCUMENTARY_DISSOLVE
        assert p.transition_duration_for(p.scenes[0]) == p.video.transition_duration_seconds

    def test_scene_override_wins(self) -> None:
        p = make_project(
            scenes=[Scene(transition_preset=TransitionPreset.FADE_THROUGH_BLACK,
                          transition_duration_seconds=1.2)]
        )
        assert p.transition_for(p.scenes[0]) is TransitionPreset.FADE_THROUGH_BLACK
        assert p.transition_duration_for(p.scenes[0]) == 1.2


class TestDurationResolution:
    def test_manual_override_wins_over_computed(self) -> None:
        scene = Scene(scene_duration_seconds=8.0, manual_duration_seconds=3.0)
        assert scene.effective_duration() == 3.0

    def test_computed_used_when_no_override(self) -> None:
        assert Scene(scene_duration_seconds=8.0).effective_duration() == 8.0

    def test_fallback_when_nothing_known(self) -> None:
        assert Scene().effective_duration(fallback=5.0) == 5.0


class TestMigrations:
    def test_current_version_is_a_noop(self) -> None:
        raw = {"schemaVersion": SCHEMA_VERSION, "name": "x"}
        assert migrate(raw)["schemaVersion"] == SCHEMA_VERSION

    def test_newer_schema_is_rejected_clearly(self) -> None:
        with pytest.raises(AppError) as exc_info:
            migrate({"schemaVersion": SCHEMA_VERSION + 5})
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert "daha yeni bir sürümüyle" in exc_info.value.message

    def test_non_integer_version_is_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            migrate({"schemaVersion": "one"})
        assert exc_info.value.code is ErrorCode.SCHEMA_VALIDATION

    def test_missing_migration_step_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A hypothetical next version with no registered upgrade path.
        monkeypatch.setattr("app.models.migrations.SCHEMA_VERSION", SCHEMA_VERSION + 1)
        with pytest.raises(AppError) as exc_info:
            migrate({"schemaVersion": SCHEMA_VERSION})
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_registered_migration_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The machinery chains one registered step per version."""
        monkeypatch.setattr("app.models.migrations.SCHEMA_VERSION", SCHEMA_VERSION + 1)
        monkeypatch.setitem(
            MIGRATIONS, SCHEMA_VERSION, lambda raw: {**raw, "addedInNext": True}
        )
        result = migrate({"schemaVersion": SCHEMA_VERSION, "name": "x"})
        assert result["addedInNext"] is True
        assert result["schemaVersion"] == SCHEMA_VERSION + 1


class TestCleanMasterMigration:
    """v1 -> v2: ``export.prepareCleanMasterForShorts``.

    New projects get it on. A project made before it existed must not be signed
    up for a second full render pass just by being opened.
    """

    def test_new_projects_prepare_a_clean_master(self) -> None:
        assert Project().export.prepare_clean_master_for_shorts is True

    def test_v1_projects_are_migrated_to_opted_out(self) -> None:
        raw = {"schemaVersion": 1, "name": "old", "slug": "old"}
        migrated = migrate(raw)
        assert migrated["schemaVersion"] == SCHEMA_VERSION
        project = Project.model_validate(migrated)
        assert project.export.prepare_clean_master_for_shorts is False

    def test_v1_export_settings_are_otherwise_untouched(self) -> None:
        raw = {
            "schemaVersion": 1,
            "name": "old",
            "export": {"quality": "standard", "keepTempFiles": True},
        }
        project = Project.model_validate(migrate(raw))
        assert project.export.quality.value == "standard"
        assert project.export.keep_temp_files is True
        assert project.export.prepare_clean_master_for_shorts is False

    def test_an_explicit_v1_opt_in_is_respected(self) -> None:
        """A hand-edited v1 file that already asked for one keeps asking."""
        raw = {
            "schemaVersion": 1,
            "name": "old",
            "export": {"prepareCleanMasterForShorts": True},
        }
        project = Project.model_validate(migrate(raw))
        assert project.export.prepare_clean_master_for_shorts is True
