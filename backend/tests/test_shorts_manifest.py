"""The render manifest: what it records, and when it refuses to be trusted."""

from __future__ import annotations

import json

import pytest

from app.errors import AppError, ErrorCode
from app.models.project import Project, Scene
from app.shorts.manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    load_manifest,
    manifest_path_for,
    verify_source,
    write_render_manifest,
)
from app.timing.schedule import build_timeline
from tests.conftest import requires_ffmpeg
from tests.shorts_factories import build_entries, make_manifest, make_source_video, write_manifest


@pytest.fixture
def manifest(tmp_path):  # noqa: ANN001, ANN201
    video = tmp_path / "the-dodo_v01.mp4"
    video.write_bytes(b"x" * 4096)
    entries, total = build_entries(scene_count=3)
    return make_manifest(video, entries=entries, total=total), video


class TestLoading:
    def test_round_trips_through_disk(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        path = write_manifest(built, video)
        assert path.name == "the-dodo_v01-manifest.json"

        loaded = load_manifest(path)
        assert loaded.schema_version == MANIFEST_SCHEMA_VERSION
        assert loaded.source.filename == video.name
        assert [e.number for e in loaded.entries] == [0, 1, 2, 3, 4]

    def test_a_missing_manifest_is_a_clear_error(self, tmp_path) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc:
            load_manifest(tmp_path / "nothing-manifest.json")
        assert exc.value.code is ErrorCode.SHORT_MANIFEST_MISSING

    def test_a_newer_schema_is_refused_rather_than_guessed_at(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        path = write_manifest(built, video)
        raw = json.loads(path.read_text("utf-8"))
        raw["schemaVersion"] = MANIFEST_SCHEMA_VERSION + 5
        path.write_text(json.dumps(raw), "utf-8")

        with pytest.raises(AppError) as exc:
            load_manifest(path)
        assert exc.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_corrupt_json_is_reported_not_swallowed(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        path = write_manifest(built, video)
        path.write_text("{not json", "utf-8")
        with pytest.raises(AppError) as exc:
            load_manifest(path)
        assert exc.value.code is ErrorCode.SHORT_MANIFEST_MISSING


class TestStaleDetection:
    """Every one of these must be a ``stale_render``, never a bad cut."""

    def test_a_deleted_source_is_stale(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        video.unlink()
        with pytest.raises(AppError) as exc:
            verify_source(built, video)
        assert exc.value.code is ErrorCode.STALE_RENDER
        assert "no longer" in exc.value.message

    def test_a_resized_source_is_stale(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        video.write_bytes(b"x" * 8192)
        with pytest.raises(AppError) as exc:
            verify_source(built, video)
        assert exc.value.code is ErrorCode.STALE_RENDER
        assert "changed" in exc.value.message

    def test_a_replaced_source_of_the_same_size_is_stale(self, manifest) -> None:  # noqa: ANN001
        built, video = manifest
        video.write_bytes(b"y" * 4096)  # same length, different bytes
        with pytest.raises(AppError) as exc:
            verify_source(built, video)
        assert exc.value.code is ErrorCode.STALE_RENDER
        assert "no longer matches" in exc.value.message

    @requires_ffmpeg
    def test_a_source_without_audio_is_refused(self, tmp_path, settings) -> None:  # noqa: ANN001
        import subprocess

        video = tmp_path / "silent.mp4"
        subprocess.run(  # noqa: S603
            [
                settings.require_tool("ffmpeg"), "-hide_banner", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-an", str(video),
            ],
            check=True, capture_output=True,
        )
        entries, total = build_entries(scene_count=1, scene_duration=1.0, with_outro=False)
        built = make_manifest(video, entries=entries, total=total, duration_seconds=2.0)

        with pytest.raises(AppError) as exc:
            verify_source(built, video, settings=settings)
        assert exc.value.code is ErrorCode.STALE_RENDER
        assert "no audio stream" in exc.value.message

    @requires_ffmpeg
    def test_a_matching_source_verifies(self, tmp_path, settings) -> None:  # noqa: ANN001
        video = make_source_video(tmp_path / "ok.mp4", seconds=2.0, settings=settings)
        entries, total = build_entries(scene_count=1, scene_duration=1.0, with_outro=False)
        built = make_manifest(video, entries=entries, total=total, duration_seconds=2.0)
        verify_source(built, video, settings=settings)  # must not raise


@requires_ffmpeg
class TestBuiltFromARealTimeline:
    def _project(self) -> Project:
        project = Project(name="Manifest Test", slug="manifest-test")
        project.intro.enabled = True
        project.intro.narration = "An intro."
        project.intro.title = "Opening"
        project.intro.manual_duration_seconds = 4.0
        project.outro.enabled = True
        project.outro.narration = "An outro."
        project.outro.manual_duration_seconds = 3.0
        project.scenes = [
            Scene(title="Habitat", manual_duration_seconds=5.0),
            Scene(title="Anatomy", manual_duration_seconds=5.0),
            Scene(title="Disabled", manual_duration_seconds=5.0, enabled=False),
        ]
        return project

    def test_numbering_matches_what_the_user_sees(self, tmp_path, settings) -> None:  # noqa: ANN001
        project = self._project()
        timeline = build_timeline(project, validate=False)
        video = make_source_video(
            tmp_path / "manifest-test_v01.mp4",
            seconds=timeline.total_duration_seconds,
            settings=settings,
        )
        from app.render.codecs import render_profile
        from app.models.enums import QualityPreset

        built = build_manifest(
            video,
            project=project,
            timeline=timeline,
            profile=render_profile(project.video, QualityPreset.PREVIEW),
            quality=QualityPreset.PREVIEW,
            checksum="",
            job_id="job-xyz",
            settings=settings,
        )

        assert [(e.kind, e.number, e.title) for e in built.entries] == [
            ("intro", 0, "Opening"),
            ("scene", 1, "Habitat"),
            ("scene", 2, "Anatomy"),
            ("outro", 3, "Outro"),
        ]
        assert built.render_job_id == "job-xyz"
        assert built.project_slug == "manifest-test"
        assert len(built.source.sha256) == 64
        # The profile records what this *render* targeted, not the project's own
        # settings: a preview caps the frame rate, and a Short cut from it has
        # to be built at the rate the file actually has.
        assert built.profile.fps == render_profile(
            project.video, QualityPreset.PREVIEW
        ).fps

    def test_safe_bounds_exclude_the_closing_fade(self, tmp_path, settings) -> None:  # noqa: ANN001
        project = self._project()
        timeline = build_timeline(project, validate=False)
        video = make_source_video(
            tmp_path / "fade_v01.mp4",
            seconds=timeline.total_duration_seconds,
            settings=settings,
        )
        from app.render.codecs import render_profile
        from app.models.enums import QualityPreset

        built = build_manifest(
            video, project=project, timeline=timeline,
            profile=render_profile(project.video, QualityPreset.PREVIEW),
            quality=QualityPreset.PREVIEW, checksum="", settings=settings,
        )
        last = built.entries[-1]
        assert last.safe_end_seconds <= built.closing_fade_start_seconds + 1e-6
        assert built.closing_fade_start_seconds < built.total_duration_seconds

    def test_writing_places_the_manifest_beside_the_export(
        self, tmp_path, settings
    ) -> None:  # noqa: ANN001
        project = self._project()
        timeline = build_timeline(project, validate=False)
        video = make_source_video(
            tmp_path / "beside_v02.mp4",
            seconds=timeline.total_duration_seconds,
            settings=settings,
        )
        from app.render.codecs import render_profile
        from app.models.enums import QualityPreset

        path = write_render_manifest(
            video, project=project, timeline=timeline,
            profile=render_profile(project.video, QualityPreset.PREVIEW),
            quality=QualityPreset.PREVIEW, checksum="", settings=settings,
        )
        assert path == manifest_path_for(video)
        assert path.parent == video.parent
        verify_source(load_manifest(path), video, settings=settings)
