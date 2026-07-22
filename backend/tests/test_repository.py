"""Project persistence: saving, backups, duplication, archiving, bundles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import AudioSource
from app.models.project import SCHEMA_VERSION, Project, Scene
from app.storage.repository import ProjectRepository
from tests.factories import write_images


@pytest.fixture
def repository(settings) -> ProjectRepository:  # noqa: ANN001
    return ProjectRepository(settings)


class TestCreateAndLoad:
    def test_create_builds_the_full_tree(self, repository: ProjectRepository) -> None:
        project = repository.create("The Dodo")
        paths = repository.paths_for(project.slug)

        assert project.slug == "the-dodo"
        assert paths.project_file.is_file()
        for directory in (paths.images, paths.imported_audio, paths.music, paths.exports):
            assert directory.is_dir()

    def test_round_trip_preserves_everything(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        project.scenes = [Scene(title="Habitat", narration="It lived on Mauritius.")]
        project.pronunciation = {"Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"}
        repository.save(project)

        reloaded = repository.load(project.slug)
        assert reloaded.scenes[0].title == "Habitat"
        assert reloaded.pronunciation["Raphus cucullatus"] == "RAH-fus koo-koo-LAH-tus"

    def test_slug_collisions_get_distinct_folders(self, repository: ProjectRepository) -> None:
        first = repository.create("Dodo")
        second = repository.create("Dodo")
        assert first.slug == "dodo"
        assert second.slug == "dodo-2"
        assert first.project_id != second.project_id

    def test_project_json_is_human_readable(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        text = repository.paths_for(project.slug).project_file.read_text("utf-8")
        assert "\n  " in text, "project.json should be indented, not minified"
        assert json.loads(text)["schemaVersion"] == SCHEMA_VERSION

    def test_missing_project_raises_a_clear_404(self, repository: ProjectRepository) -> None:
        with pytest.raises(AppError) as exc_info:
            repository.load("does-not-exist")
        assert exc_info.value.code is ErrorCode.PROJECT_NOT_FOUND
        assert exc_info.value.http_status == 404


class TestAtomicSaveAndBackups:
    def test_save_creates_a_backup_of_the_previous_state(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        project.metadata.video_title = "First"
        repository.save(project)
        project.metadata.video_title = "Second"
        repository.save(project)

        backups = repository.list_backups(project.slug)
        assert len(backups) == 2
        assert repository.load(project.slug).metadata.video_title == "Second"

    def test_restore_undoes_the_most_recent_save(self, repository: ProjectRepository) -> None:
        """The newest backup holds the state from immediately before the last save."""
        project = repository.create("Dodo")
        project.metadata.video_title = "Original"
        repository.save(project)
        project.metadata.video_title = "Broken edit"
        repository.save(project)

        newest = repository.list_backups(project.slug)[0]
        restored = repository.restore_backup(project.slug, newest)
        assert restored.metadata.video_title == "Original"

    def test_restore_is_itself_reversible(self, repository: ProjectRepository) -> None:
        """Restoring backs up the current state first, so it is never a one-way door."""
        project = repository.create("Dodo")
        project.metadata.video_title = "Original"
        repository.save(project)
        project.metadata.video_title = "Broken edit"
        repository.save(project)

        before = len(repository.list_backups(project.slug))
        repository.restore_backup(project.slug, repository.list_backups(project.slug)[0])
        after = repository.list_backups(project.slug)

        assert len(after) == before + 1
        # The state we restored away from is still recoverable.
        recovered = repository.restore_backup(project.slug, after[0])
        assert recovered.metadata.video_title == "Broken edit"

    def test_backups_are_capped(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        for index in range(25):
            project.metadata.video_title = f"v{index}"
            repository.save(project)
        assert len(repository.list_backups(project.slug)) <= 20

    def test_no_temp_file_is_left_behind(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        repository.save(project)
        leftovers = list(repository.paths_for(project.slug).root.glob("*.tmp"))
        assert leftovers == []

    def test_corrupt_project_reports_the_json_error(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        repository.paths_for(project.slug).project_file.write_text("{ not json", "utf-8")
        with pytest.raises(AppError) as exc_info:
            repository.load(project.slug)
        assert exc_info.value.code is ErrorCode.INVALID_JSON
        assert "backup" in exc_info.value.suggestion.lower()

    def test_schema_violation_lists_the_offending_fields(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        path = repository.paths_for(project.slug).project_file
        raw = json.loads(path.read_text("utf-8"))
        raw["video"]["fps"] = 9999
        path.write_text(json.dumps(raw), "utf-8")

        with pytest.raises(AppError) as exc_info:
            repository.load(project.slug)
        assert exc_info.value.code is ErrorCode.SCHEMA_VALIDATION
        assert "video.fps" in (exc_info.value.details or "")


class TestListing:
    def test_lists_newest_first(self, repository: ProjectRepository) -> None:
        repository.create("Alpha")
        second = repository.create("Beta")
        summaries = repository.list_projects()
        assert summaries[0].slug == second.slug

    def test_corrupt_project_still_appears(self, repository: ProjectRepository) -> None:
        """A broken project must be visible so the user can fix it, not vanish."""
        project = repository.create("Dodo")
        repository.paths_for(project.slug).project_file.write_text("broken", "utf-8")
        summaries = repository.list_projects()
        assert any("unreadable" in s.name for s in summaries)


class TestDuplicate:
    def test_copies_user_content_but_not_generated_audio(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 3)
        paths.generated_audio.mkdir(parents=True, exist_ok=True)
        (paths.generated_audio / "scene-1.mp3").write_bytes(b"fake")

        project.scenes = [
            Scene(audio_source=AudioSource.GENERATED, audio_file="audio/generated/scene-1.mp3")
        ]
        repository.save(project)

        copy = repository.duplicate(project.slug, "Dodo Remix")
        copy_paths = repository.paths_for(copy.slug)

        assert copy.slug == "dodo-remix"
        assert len(list(copy_paths.images.iterdir())) == 3, "user images must be copied"
        assert copy.project_id != project.project_id
        # Generated audio is derived: the copy regenerates rather than inheriting.
        assert copy.scenes[0].audio_file is None

    def test_original_is_untouched(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        write_images(repository.paths_for(project.slug).images, 2)
        repository.duplicate(project.slug, "Copy")
        assert len(list(repository.paths_for(project.slug).images.iterdir())) == 2


class TestArchive:
    def test_archive_and_unarchive_round_trip(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        repository.archive(project.slug)

        assert not (repository.root / project.slug).exists()
        assert any(s.archived for s in repository.list_projects())

        restored = repository.unarchive(project.slug)
        assert (repository.root / restored.slug).exists()
        assert not any(s.archived for s in repository.list_projects())

    def test_archived_projects_can_be_hidden_from_the_list(
        self, repository: ProjectRepository
    ) -> None:
        project = repository.create("Dodo")
        repository.archive(project.slug)
        assert repository.list_projects(include_archived=False) == []


class TestCleanDerived:
    def test_removes_derived_but_never_user_content(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        write_images(paths.images, 2)
        (paths.imported_audio / "mine.wav").write_bytes(b"user audio")
        (paths.clips / "scene-1.mp4").write_bytes(b"derived")
        (paths.generated_audio / "scene-1.mp3").write_bytes(b"derived")

        removed = repository.clean_derived(project.slug)

        assert removed == 2
        assert len(list(paths.images.iterdir())) == 2
        assert (paths.imported_audio / "mine.wav").exists()
        assert not (paths.clips / "scene-1.mp4").exists()
        assert paths.project_file.exists()


class TestBundles:
    def test_export_then_import_reproduces_the_project(
        self, repository: ProjectRepository, tmp_path: Path
    ) -> None:
        project = repository.create("Dodo")
        write_images(repository.paths_for(project.slug).images, 3)
        project.scenes = [Scene(title="Habitat", narration="Mauritius.")]
        project.metadata.video_title = "The Dodo"
        repository.save(project)

        bundle = repository.export_bundle(project.slug, tmp_path / "dodo.zip")
        assert bundle.is_file()

        imported = repository.import_bundle(bundle, name="Dodo Restored")
        imported_paths = repository.paths_for(imported.slug)

        assert imported.metadata.video_title == "The Dodo"
        assert imported.scenes[0].title == "Habitat"
        assert len(list(imported_paths.images.iterdir())) == 3
        assert imported.slug != project.slug

    def test_bundle_excludes_derived_by_default(
        self, repository: ProjectRepository, tmp_path: Path
    ) -> None:
        import zipfile

        project = repository.create("Dodo")
        paths = repository.paths_for(project.slug)
        (paths.clips / "big.mp4").write_bytes(b"x" * 1000)

        bundle = repository.export_bundle(project.slug, tmp_path / "dodo.zip")
        with zipfile.ZipFile(bundle) as archive:
            assert not any(n.startswith("derived/") for n in archive.namelist())

    def test_non_zip_is_rejected_clearly(
        self, repository: ProjectRepository, tmp_path: Path
    ) -> None:
        bogus = tmp_path / "not-a-bundle.zip"
        bogus.write_text("hello")
        with pytest.raises(AppError) as exc_info:
            repository.import_bundle(bogus)
        assert exc_info.value.code is ErrorCode.INVALID_JSON

    def test_zip_without_project_json_is_rejected(
        self, repository: ProjectRepository, tmp_path: Path
    ) -> None:
        import zipfile

        bogus = tmp_path / "empty.zip"
        with zipfile.ZipFile(bogus, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        with pytest.raises(AppError) as exc_info:
            repository.import_bundle(bogus)
        assert exc_info.value.code is ErrorCode.SCHEMA_VALIDATION

    def test_zip_slip_is_rejected(self, repository: ProjectRepository, tmp_path: Path) -> None:
        """A bundle must not be able to write outside its project folder."""
        import zipfile

        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("project.json", json.dumps(Project(name="Evil").model_dump(mode="json")))
            archive.writestr("../../../../tmp/pwned.txt", "escaped")

        with pytest.raises(AppError) as exc_info:
            repository.import_bundle(evil)
        assert exc_info.value.code is ErrorCode.PATH_TRAVERSAL
        assert not Path("/tmp/pwned.txt").exists()


class TestDelete:
    def test_delete_removes_the_folder(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        root = repository.paths_for(project.slug).root
        repository.delete(project.slug)
        assert not root.exists()
