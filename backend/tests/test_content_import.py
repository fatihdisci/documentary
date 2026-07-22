"""Content package parsing, import, and image-to-scene mapping."""

from __future__ import annotations

import json

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import AnimationPreset
from app.storage.content_import import (
    apply_content,
    available_images,
    map_images_to_scenes,
    parse_content_json,
)
from app.storage.repository import ProjectRepository
from tests.factories import load_dodo_package, write_images


@pytest.fixture
def repository(settings) -> ProjectRepository:  # noqa: ANN001
    return ProjectRepository(settings)


class TestParsing:
    def test_bundled_dodo_package_is_valid(self) -> None:
        """The shipped example must always parse — it is the documentation."""
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        assert package.common_name == "Dodo"
        assert package.scientific_name == "Raphus cucullatus"
        assert len(package.scenes) == 10
        assert package.intro.narration
        assert package.outro.narration
        assert package.pronunciation["Raphus cucullatus"] == "RAH-fus koo-koo-LAH-tus"

    def test_every_dodo_scene_has_narration_and_a_prompt(self) -> None:
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        for index, scene in enumerate(package.scenes):
            assert scene.narration.strip(), f"scene {index} has no narration"
            assert scene.image_prompt.strip(), f"scene {index} has no image prompt"
            assert scene.title.strip(), f"scene {index} has no title"

    def test_syntax_error_points_at_the_line(self) -> None:
        bad = '{\n  "commonName": "Dodo",\n  "scenes": [,]\n}'
        with pytest.raises(AppError) as exc_info:
            parse_content_json(bad, max_bytes=1_000_000)
        error = exc_info.value
        assert error.code is ErrorCode.INVALID_JSON
        assert "line 3" in error.message
        assert ">>" in (error.details or ""), "should show the offending line"

    def test_schema_errors_list_every_field(self) -> None:
        bad = json.dumps({"commonName": "Dodo", "scenes": [{"focusX": 5.0, "focusY": -1.0}]})
        with pytest.raises(AppError) as exc_info:
            parse_content_json(bad, max_bytes=1_000_000)
        details = exc_info.value.details or ""
        assert "scenes.0.focusX" in details
        assert "scenes.0.focusY" in details

    def test_empty_scene_list_is_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_content_json(json.dumps({"commonName": "Dodo", "scenes": []}), max_bytes=1_000_000)
        assert exc_info.value.code is ErrorCode.SCHEMA_VALIDATION

    def test_top_level_array_is_rejected_with_a_useful_message(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_content_json("[]", max_bytes=1_000_000)
        assert "object at the top level" in exc_info.value.message

    def test_oversized_file_is_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_content_json(json.dumps(load_dodo_package()), max_bytes=100)
        assert exc_info.value.code is ErrorCode.FILE_TOO_LARGE

    def test_unknown_fields_are_ignored_not_fatal(self) -> None:
        """A package from a newer generator should still import what we understand."""
        payload = load_dodo_package()
        payload["someFutureField"] = {"nested": True}
        payload["scenes"][0]["futureHint"] = "ignore me"
        package = parse_content_json(json.dumps(payload), max_bytes=10_000_000)
        assert len(package.scenes) == 10


class TestApplyContent:
    def test_populates_the_whole_project(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        assert project.animal.common_name == "Dodo"
        assert project.animal.scientific_name == "Raphus cucullatus"
        assert project.metadata.video_title.startswith("The Dodo")
        assert project.metadata.thumbnail_text == "GONE IN 100 YEARS"
        assert len(project.scenes) == 10
        assert report.scenes_created == 10
        assert project.intro.narration
        assert project.outro.hook_text == "Next: The Thylacine"
        assert project.pronunciation["Mauritius"] == "muh-RISH-us"

    def test_carries_framing_hints_onto_scenes(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=repository.paths_for(project.slug))

        diet_scene = project.scenes[3]
        assert diet_scene.animation_preset is AnimationPreset.ZOOM_TO_FOCUS
        assert diet_scene.focus_x == pytest.approx(0.38)
        assert diet_scene.focus_y == pytest.approx(0.55)

    def test_does_not_touch_render_settings(self, repository: ProjectRepository) -> None:
        """Import brings content, never overrides how the user configured output."""
        project = repository.create("Dodo")
        project.video.fps = 30
        project.video.target_duration_seconds = 420.0
        project.audio.voice = "en-GB-SoniaNeural"

        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=repository.paths_for(project.slug))

        assert project.video.fps == 30
        assert project.video.target_duration_seconds == 420.0
        assert project.audio.voice == "en-GB-SoniaNeural"

    def test_update_mode_preserves_per_scene_tuning(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=paths)

        # The user hand-tunes a scene, then re-imports an updated package.
        project.scenes[0].manual_duration_seconds = 12.0
        project.scenes[0].audio_file = "audio/imported/mine.wav"
        scene_id = project.scenes[0].id

        apply_content(project, package, paths=paths, replace_scenes=False)

        assert project.scenes[0].id == scene_id, "scene identity should survive an update"
        assert project.scenes[0].manual_duration_seconds == 12.0
        assert project.scenes[0].audio_file == "audio/imported/mine.wav"

    def test_replace_mode_starts_clean(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=paths)
        original_id = project.scenes[0].id

        report = apply_content(project, package, paths=paths, replace_scenes=True)

        assert project.scenes[0].id != original_id
        assert report.scenes_created == 10
        assert len(project.scenes) == 10

    def test_extra_scenes_are_kept_in_update_mode(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        payload = load_dodo_package()
        apply_content(
            project, parse_content_json(json.dumps(payload), max_bytes=10_000_000), paths=paths
        )

        shorter = {**payload, "scenes": payload["scenes"][:6]}
        report = apply_content(
            project,
            parse_content_json(json.dumps(shorter), max_bytes=10_000_000),
            paths=paths,
            replace_scenes=False,
        )

        assert len(project.scenes) == 10, "existing work should not be deleted silently"
        assert any("were kept" in w for w in report.warnings)
        assert [s.order for s in project.scenes] == list(range(10))


class TestImageMapping:
    def test_maps_in_natural_filename_order(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        assert report.images_mapped == 10
        assert project.scenes[0].image_file == "01-opening.png"
        assert project.scenes[9].image_file == "10-conservation.png"
        assert report.unmapped_scenes == []
        assert report.unused_images == []

    def test_double_digit_names_sort_correctly(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 12)
        names = available_images(paths)
        assert names[1] == "02-habitat.png"
        assert names[9] == "10-conservation.png"
        assert names[11] == "12-scene-12.png"

    def test_too_few_images_reports_which_scenes_are_bare(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 4)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        assert report.images_mapped == 4
        assert report.unmapped_scenes == [4, 5, 6, 7, 8, 9]
        assert any("6 scene(s) have no image" in w for w in report.warnings)

    def test_extra_images_are_reported_not_discarded(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 13)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        # The surplus feeds the intro first, then the ten scenes; two are spare.
        assert report.images_mapped == 11
        assert report.intro_image == "01-opening.png"
        assert len(report.unused_images) == 2
        assert any("not used" in w for w in report.warnings)

    def test_intro_takes_its_own_first_image_when_there_is_a_surplus(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 11)  # one more than the ten scenes
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        assert report.intro_image == "01-opening.png"
        assert project.intro.image_file == "01-opening.png"
        assert project.scenes[0].image_file == "02-habitat.png"
        assert report.images_mapped == 11
        assert report.unmapped_scenes == []
        # The opening and the first scene never show the same picture again.
        assert project.intro.image_file != project.scenes[0].image_file

    def test_intro_reuses_first_scene_without_a_surplus(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)  # exactly one per scene, none to spare
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        # No spare image: the intro is left to reuse the first scene at render
        # time, exactly as before, so a ten-image project is unaffected.
        assert report.intro_image is None
        assert project.intro.image_file is None
        assert project.scenes[0].image_file == "01-opening.png"
        assert report.images_mapped == 10

    def test_manual_remapping_survives_a_remap(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=paths)

        project.scenes[0].image_file = "05-arrival.png"
        map_images_to_scenes(project, paths)

        assert project.scenes[0].image_file == "05-arrival.png"

    def test_force_remap_overrides_manual_choices(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)
        apply_content(project, package, paths=paths)
        project.scenes[0].image_file = "05-arrival.png"

        map_images_to_scenes(project, paths, force=True)

        assert project.scenes[0].image_file == "01-opening.png"

    def test_no_images_warns_instead_of_failing(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        package = parse_content_json(json.dumps(load_dodo_package()), max_bytes=10_000_000)

        report = apply_content(project, package, paths=paths)

        assert report.images_mapped == 0
        assert any("No images have been uploaded" in w for w in report.warnings)
        assert len(project.scenes) == 10, "scenes should still be created"

    def test_explicit_image_file_in_package_is_honoured(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)
        payload = load_dodo_package()
        payload["scenes"][0]["imageFile"] = "09-bones.png"

        report = apply_content(
            project, parse_content_json(json.dumps(payload), max_bytes=10_000_000), paths=paths
        )

        assert project.scenes[0].image_file == "09-bones.png"
        # The image it claimed is not handed out twice.
        assert [s.image_file for s in project.scenes].count("09-bones.png") == 1
        assert report.images_mapped == 9

    def test_missing_referenced_image_is_reported(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 10)
        payload = load_dodo_package()
        payload["scenes"][0]["imageFile"] = "does-not-exist.png"

        report = apply_content(
            project, parse_content_json(json.dumps(payload), max_bytes=10_000_000), paths=paths
        )

        assert any("does-not-exist.png" in w for w in report.warnings)
