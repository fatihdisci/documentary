"""The Shorts-ready source package: capability, verification and refusal.

The rule this file exists to hold: **a render with burned-in captions and no
clean master can never produce Shorts-native captions, and is never quietly
processed as if it could.** Every failure path here has to end in a message the
user can act on, not in a fallback that would caption the same Short twice.
"""

from __future__ import annotations

import json

import pytest

from app.errors import AppError, ErrorCode
from app.shorts.cues import (
    CUE_SIDECAR_SCHEMA_VERSION,
    build_sidecar,
    cue_content_hash,
    load_sidecar,
    sidecar_path_for,
    write_sidecar,
)
from app.shorts.manifest import (
    MANIFEST_SCHEMA_VERSION,
    SHORTS_SOURCE_PACKAGE_VERSION,
    load_manifest,
    verify_clean_master,
)
from app.shorts.models import ShortCaptionMode, ShortRequest
from app.shorts.service import ShortsService
from app.storage.repository import ProjectRepository
from app.timing.subtitles import Cue
from tests.conftest import requires_ffmpeg
from tests.shorts_factories import (
    build_entries,
    cues_for,
    make_manifest,
    make_shorts_source,
    make_source_video,
    request_for,
    write_manifest,
    write_shorts_source,
)


@pytest.fixture
def project(settings):  # noqa: ANN001, ANN201
    repository = ProjectRepository(settings)
    created = repository.create("The Dodo")
    return created, repository.paths_for(created.slug)


def _fake_video(path, size: int = 8192):  # noqa: ANN001, ANN202
    """A file of the right shape but not real video.

    Enough for every check that happens *before* ffprobe — existence, size,
    checksum, schema and identity — which is most of them, and keeps those tests
    running in milliseconds without FFmpeg.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"v" * size)
    return path


#: Two short scenes with a dissolve between them. Small enough that the "real
#: video" variants below encode in well under a second.
SCENE_SECONDS = 2.0
SCENE_COUNT = 2
TOTAL_SECONDS = 3.5
VIDEO_WIDTH = 320
VIDEO_HEIGHT = 180


def _render(  # noqa: ANN202
    paths,  # noqa: ANN001
    *,
    with_package: bool,
    burned_in: bool = True,
    cues=None,  # noqa: ANN001
    real: bool = False,
    settings=None,  # noqa: ANN001
):
    """A finished render on disk, with or without a Shorts source package.

    ``real`` encodes genuine H.264+AAC files, which is only needed by tests that
    reach ffprobe — that is, the ones asserting something *works* rather than
    something is refused.
    """
    entries, total = build_entries(
        scene_count=SCENE_COUNT,
        scene_duration=SCENE_SECONDS,
        with_intro=False,
        with_outro=False,
    )
    export_path = paths.exports / "the-dodo_v01.mp4"
    master_path = paths.shorts_source / "the-dodo_v01-clean.mp4"

    if real:
        export = make_source_video(
            export_path, seconds=total, width=VIDEO_WIDTH, height=VIDEO_HEIGHT,
            settings=settings,
        )
    else:
        export = _fake_video(export_path)

    manifest = make_manifest(
        export,
        entries=entries,
        total=total,
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
        duration_seconds=total,
        source_has_burned_in_subtitles=burned_in,
    )

    if with_package:
        if real:
            master = make_source_video(
                master_path, seconds=total, width=VIDEO_WIDTH, height=VIDEO_HEIGHT,
                settings=settings,
            )
        else:
            master = _fake_video(master_path, size=8000)
        package, sidecar = make_shorts_source(
            master,
            manifest=manifest,
            cues=cues if cues is not None else cues_for(entries),
        )
        manifest.shorts_source = write_shorts_source(package, sidecar, master)

    write_manifest(manifest, export)
    return manifest, export


class TestCapabilityReporting:
    def test_a_legacy_render_reports_no_native_captions(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=False)

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is False
        assert support.source_has_burned_in_subtitles is True
        assert "yeniden oluşturun" in (support.reason or "")
        assert "altyazısız kopya" in (support.reason or "")

    @requires_ffmpeg
    def test_a_prepared_render_reports_native_captions(self, project, settings) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True, real=True, settings=settings)

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is True
        assert support.reason is None
        assert support.cue_count > 0
        assert support.clean_master_filename == "the-dodo_v01-clean.mp4"
        assert support.cue_schema_version == CUE_SIDECAR_SCHEMA_VERSION

    @requires_ffmpeg
    def test_a_render_with_no_narration_says_there_is_nothing_to_draw(
        self, project, settings
    ) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(
            paths, with_package=True, cues=[], real=True, settings=settings
        )

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is False
        assert "konuşma altyazısı yok" in (support.reason or "")

    @requires_ffmpeg
    def test_a_missing_cue_sidecar_is_reported(self, project, settings) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True, real=True, settings=settings)
        sidecar_path_for(paths.shorts_source / "the-dodo_v01-clean.mp4").unlink()

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is False
        assert "altyazı verisi" in (support.reason or "")

    def test_a_missing_clean_master_is_reported_not_ignored(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        (paths.shorts_source / "the-dodo_v01-clean.mp4").unlink()

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is False
        assert "artık diskte yok" in (support.reason or "")

    def test_a_resized_clean_master_is_reported(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        (paths.shorts_source / "the-dodo_v01-clean.mp4").write_bytes(b"different")

        support = ShortsService().caption_support(paths, manifest)

        assert support.native_available is False
        assert "değişmiş" in (support.reason or "")


class TestVerification:
    def test_a_render_without_a_package_raises_captions_unavailable(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=False)

        with pytest.raises(AppError) as exc:
            verify_clean_master(manifest, paths.shorts_source / "nothing.mp4")
        assert exc.value.code is ErrorCode.SHORT_CAPTIONS_UNAVAILABLE
        assert "görüntünün içine gömülü" in exc.value.message

    def test_a_package_from_a_newer_app_is_refused(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        manifest.shorts_source.package_version = SHORTS_SOURCE_PACKAGE_VERSION + 1

        with pytest.raises(AppError) as exc:
            verify_clean_master(
                manifest, paths.shorts_source / "the-dodo_v01-clean.mp4"
            )
        assert exc.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_a_package_from_another_render_is_refused(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        manifest.shorts_source.render_job_id = "some-other-render"

        with pytest.raises(AppError) as exc:
            verify_clean_master(
                manifest, paths.shorts_source / "the-dodo_v01-clean.mp4"
            )
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE
        assert "başka bir videoya ait" in exc.value.message

    def test_a_package_from_another_project_snapshot_is_refused(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        manifest.shorts_source.project_snapshot_sha256 = "b" * 64

        with pytest.raises(AppError) as exc:
            verify_clean_master(
                manifest, paths.shorts_source / "the-dodo_v01-clean.mp4"
            )
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE
        assert "başka bir hâlinden" in exc.value.message

    def test_a_tampered_checksum_is_caught(self, project) -> None:  # noqa: ANN001
        _, paths = project
        manifest, _ = _render(paths, with_package=True)
        manifest.shorts_source.clean_master.sha256 = "c" * 64

        with pytest.raises(AppError) as exc:
            verify_clean_master(
                manifest,
                paths.shorts_source / "the-dodo_v01-clean.mp4",
                check_checksum=True,
            )
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE


class TestCueSidecar:
    def test_it_round_trips_with_absolute_times_and_units(self, tmp_path) -> None:  # noqa: ANN001
        cues = [
            Cue(index=1, start_seconds=1.5, end_seconds=3.25, lines=["Line one", "line two"]),
            Cue(index=2, start_seconds=3.5, end_seconds=5.0, lines=["Line three"]),
        ]
        sidecar = build_sidecar(
            project_slug="the-dodo",
            cues=cues,
            cues_by_unit={"scene-1": cues[:1], "scene-2": cues[1:]},
            clean_master_sha256="d" * 64,
            total_duration_seconds=42.0,
            render_job_id="render0001",
            timing_source="measured-words",
        )
        target = tmp_path / "x-shorts-cues.json"
        ref = write_sidecar(sidecar, target)

        loaded = load_sidecar(target, ref)

        assert loaded.schema_version == CUE_SIDECAR_SCHEMA_VERSION
        assert loaded.clean_master_sha256 == "d" * 64
        assert [c.unit_id for c in loaded.cues] == ["scene-1", "scene-2"]
        assert loaded.cues[0].start_seconds == 1.5
        assert loaded.cues[0].lines == ["Line one", "line two"]
        assert ref.cue_count == 2
        assert ref.timing_source == "measured-words"

    def test_identical_captions_hash_identically(self) -> None:
        """Rewriting the same captions must not invalidate a finished Short."""
        cues = cues_for(build_entries(scene_count=2)[0])
        assert cue_content_hash(cues) == cue_content_hash(list(cues))

    def test_changed_text_changes_the_content_hash(self) -> None:
        cues = cues_for(build_entries(scene_count=2)[0])
        edited = [*cues[:-1], cues[-1].model_copy(update={"lines": ["different"]})]
        assert cue_content_hash(cues) != cue_content_hash(edited)

    def test_an_edited_sidecar_file_is_refused(self, tmp_path) -> None:  # noqa: ANN001
        cues = [Cue(index=1, start_seconds=1.0, end_seconds=2.0, lines=["hi"])]
        sidecar = build_sidecar(
            project_slug="the-dodo",
            cues=cues,
            cues_by_unit={"scene-1": cues},
            clean_master_sha256="d" * 64,
            total_duration_seconds=10.0,
        )
        target = tmp_path / "x-shorts-cues.json"
        ref = write_sidecar(sidecar, target)

        raw = json.loads(target.read_text("utf-8"))
        raw["cues"][0]["lines"] = ["something else entirely"]
        target.write_text(json.dumps(raw), "utf-8")

        with pytest.raises(AppError) as exc:
            load_sidecar(target, ref)
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE

    def test_a_sidecar_from_a_newer_app_is_refused(self, tmp_path) -> None:  # noqa: ANN001
        cues = [Cue(index=1, start_seconds=1.0, end_seconds=2.0, lines=["hi"])]
        sidecar = build_sidecar(
            project_slug="the-dodo",
            cues=cues,
            cues_by_unit={"scene-1": cues},
            clean_master_sha256="d" * 64,
            total_duration_seconds=10.0,
        )
        target = tmp_path / "x-shorts-cues.json"
        write_sidecar(sidecar, target)
        raw = json.loads(target.read_text("utf-8"))
        raw["schemaVersion"] = CUE_SIDECAR_SCHEMA_VERSION + 1
        target.write_text(json.dumps(raw), "utf-8")

        with pytest.raises(AppError) as exc:
            load_sidecar(target)
        assert exc.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_a_missing_sidecar_says_what_to_do(self, tmp_path) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc:
            load_sidecar(tmp_path / "gone-shorts-cues.json")
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE
        assert "yeniden oluşturun" in (exc.value.suggestion or "")


@requires_ffmpeg
class TestPreflightRefusal:
    """Preflight reads the real files, so these need real files."""

    def native(self, *unit_ids: str) -> ShortRequest:
        return ShortRequest(
            source_render_id="render0001",
            segments=request_for(*unit_ids).segments,
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
        )

    def test_native_captions_are_blocked_on_a_legacy_render(self, project, settings) -> None:  # noqa: ANN001
        created, paths = project
        _render(paths, with_package=False, real=True, settings=settings)

        response = ShortsService().preflight(
            created.slug, self.native("scene-1", "scene-2"), with_frames=False
        )

        assert response.ready is False
        assert any("altyazısız kopya" in issue for issue in response.blocking_issues)
        assert response.caption_support.native_available is False
        assert response.caption_mode is ShortCaptionMode.SHORTS_NATIVE

    def test_the_legacy_mode_still_works_on_the_same_render(self, project, settings) -> None:  # noqa: ANN001
        """Refusing native captions must not take legacy Shorts away."""
        created, paths = project
        _render(paths, with_package=False, real=True, settings=settings)

        response = ShortsService().preflight(
            created.slug, request_for("scene-1", "scene-2"), with_frames=False
        )

        assert response.ready is True
        assert response.blocking_issues == []
        assert response.caption_mode is ShortCaptionMode.SOURCE_BURNED_IN
        assert response.plan is not None

    def test_native_captions_pass_preflight_on_a_prepared_render(self, project, settings) -> None:  # noqa: ANN001
        created, paths = project
        _render(paths, with_package=True, real=True, settings=settings)

        response = ShortsService().preflight(
            created.slug, self.native("scene-1", "scene-2"), with_frames=False
        )

        assert response.ready is True
        assert response.caption_support.native_available is True
        assert response.caption_cue_count > 0
        assert response.caption_style is not None

    def test_a_stale_clean_master_blocks_rather_than_falling_back(self, project, settings) -> None:  # noqa: ANN001
        """The critical one: no silent fall back to the burned-in source."""
        created, paths = project
        _render(paths, with_package=True, real=True, settings=settings)
        (paths.shorts_source / "the-dodo_v01-clean.mp4").unlink()

        response = ShortsService().preflight(
            created.slug, self.native("scene-1"), with_frames=False
        )

        assert response.ready is False
        assert response.blocking_issues, "a stale clean master must block the job"
        assert response.caption_support.native_available is False

    def test_the_source_list_advertises_the_capability(self, project, settings) -> None:  # noqa: ANN001
        created, paths = project
        _render(paths, with_package=True, real=True, settings=settings)

        sources = ShortsService().list_sources(created.slug)

        assert len(sources) == 1
        assert sources[0].captions.native_available is True
        assert sources[0].usable is True


class TestLegacyManifests:
    """A v1 manifest predates every field above and must keep working."""

    def test_a_v1_manifest_still_loads(self, project) -> None:  # noqa: ANN001
        _, paths = project
        export = _fake_video(paths.exports / "the-dodo_v01.mp4")
        entries, total = build_entries(scene_count=3)
        manifest = make_manifest(export, entries=entries, total=total, schema_version=1)
        path = write_manifest(manifest, export)

        loaded = load_manifest(path)

        assert loaded.schema_version == 1
        assert loaded.shorts_source is None
        assert [e.number for e in loaded.entries] == [0, 1, 2, 3, 4]

    def test_a_v1_manifest_on_disk_has_no_v2_fields(self, project) -> None:  # noqa: ANN001
        """Written by an older build: the keys are simply absent."""
        _, paths = project
        export = _fake_video(paths.exports / "the-dodo_v01.mp4")
        entries, total = build_entries(scene_count=3)
        manifest = make_manifest(export, entries=entries, total=total, schema_version=1)
        path = write_manifest(manifest, export)

        raw = json.loads(path.read_text("utf-8"))
        del raw["shortsSource"]
        del raw["sourceHasBurnedInSubtitles"]
        path.write_text(json.dumps(raw), "utf-8")

        loaded = load_manifest(path)

        assert loaded.shorts_source is None
        # Historical default: burn-in was on, and those pixels are permanent.
        assert loaded.source_has_burned_in_subtitles is True
        assert loaded.supports_native_captions is False

    @requires_ffmpeg
    def test_a_v1_render_still_lists_and_plans_as_a_source(self, project, settings) -> None:  # noqa: ANN001
        created, paths = project
        entries, total = build_entries(
            scene_count=SCENE_COUNT, scene_duration=SCENE_SECONDS,
            with_intro=False, with_outro=False,
        )
        export = make_source_video(
            paths.exports / "the-dodo_v01.mp4", seconds=total,
            width=VIDEO_WIDTH, height=VIDEO_HEIGHT, settings=settings,
        )
        write_manifest(
            make_manifest(
                export, entries=entries, total=total, schema_version=1,
                width=VIDEO_WIDTH, height=VIDEO_HEIGHT, duration_seconds=total,
            ),
            export,
        )

        service = ShortsService()
        sources = service.list_sources(created.slug)
        assert [s.render_id for s in sources] == ["render0001"]
        assert sources[0].captions.native_available is False

        response = service.preflight(
            created.slug, request_for("scene-1"), with_frames=False
        )
        assert response.ready is True

    def test_the_current_version_is_two(self) -> None:
        assert MANIFEST_SCHEMA_VERSION == 2


@requires_ffmpeg
class TestRealCleanMaster:
    """The parts that need a real file: ffprobe identity checks."""

    def test_a_clean_master_of_the_wrong_shape_is_refused(self, project, settings) -> None:  # noqa: ANN001
        _, paths = project
        export = make_source_video(
            paths.exports / "the-dodo_v01.mp4", seconds=3.0, width=640, height=360
        )
        entries, total = build_entries(
            scene_count=1, scene_duration=1.0, with_intro=False, with_outro=False
        )
        manifest = make_manifest(
            export, entries=entries, total=total, width=640, height=360,
            duration_seconds=3.0,
        )
        # Same content, different geometry: not the same cut.
        master = make_source_video(
            paths.shorts_source / "the-dodo_v01-clean.mp4",
            seconds=3.0, width=320, height=180,
        )
        package, sidecar = make_shorts_source(master, manifest=manifest)
        package.clean_master.width = 320
        package.clean_master.height = 180
        manifest.shorts_source = write_shorts_source(package, sidecar, master)

        with pytest.raises(AppError) as exc:
            verify_clean_master(manifest, master, settings=settings)
        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE
        assert "boyut" in (exc.value.details or "")
