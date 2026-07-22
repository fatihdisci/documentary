"""Shorts API: source listing, timelines, idempotency, deletion and path safety.

Most of these need no FFmpeg: source discovery, numbering, selection ordering,
cache reuse and deletion are all decided from manifests on disk. The tests that
genuinely need to cut video live in ``test_shorts_render.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.enums import JobStatus
from app.shorts.models import (
    ShortLayout,
    ShortManifest,
    ShortPlan,
    ShortRequest,
    ShortSegmentPlan,
    ShortSegmentRequest,
)
from app.storage.repository import ProjectRepository
from tests.shorts_factories import build_entries, make_manifest, write_manifest


@pytest.fixture(autouse=True)
def fresh_managers():  # noqa: ANN201
    from app.render.jobs import reset_job_manager
    from app.render.slot import reset_render_slot
    from app.shorts.jobs import reset_short_job_manager

    reset_job_manager()
    reset_short_job_manager()
    reset_render_slot()
    yield
    reset_job_manager()
    reset_short_job_manager()
    reset_render_slot()


def make_project(settings, name: str = "Shorts API") -> tuple[str, Path]:  # noqa: ANN001
    repository = ProjectRepository(settings)
    project = repository.create(name)
    paths = repository.paths_for(project.slug)
    paths.ensure()
    return project.slug, paths.root


def add_source(
    settings,  # noqa: ANN001
    slug: str,
    *,
    render_id: str = "render0001",
    version: int = 1,
    scene_count: int = 4,
    scene_duration: float = 10.0,
):  # noqa: ANN201
    """A stand-in export plus its manifest. No FFmpeg needed to describe it."""
    paths = ProjectRepository(settings).paths_for(slug)
    paths.ensure()
    video = paths.exports / f"{slug}_v{version:02d}.mp4"
    # Distinct bytes per version, so two "renders" really do hash differently.
    video.write_bytes(bytes([version % 256]) * 20_000)
    entries, total = build_entries(scene_count=scene_count, scene_duration=scene_duration)
    manifest = make_manifest(
        video, slug=slug, render_job_id=render_id, entries=entries, total=total
    )
    write_manifest(manifest, video)
    return manifest, video


class TestSources:
    def test_lists_a_completed_render_with_its_details(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        manifest, video = add_source(settings, slug)

        body = client.get(f"/api/projects/{slug}/shorts/sources").json()
        assert len(body) == 1
        source = body[0]
        assert source["renderId"] == "render0001"
        assert source["filename"] == video.name
        assert source["status"] == "completed"
        assert source["usable"] is True
        assert source["sectionCount"] == len(manifest.entries)
        assert source["durationSeconds"] > 0
        assert source["sizeBytes"] == video.stat().st_size
        assert source["quality"]
        assert source["thumbnailUrl"].endswith("/poster")

    def test_an_export_without_a_manifest_is_not_offered(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, root = make_project(settings)
        (root / "exports" / f"{slug}_v09.mp4").write_bytes(b"x" * 5000)
        assert client.get(f"/api/projects/{slug}/shorts/sources").json() == []

    def test_a_render_whose_job_did_not_complete_is_not_offered(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        add_source(settings, slug, render_id="failed01")

        # Put a matching, *failed* render job into the long-render history.
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "failed01.json").write_text(
            json.dumps(
                {
                    "id": "failed01",
                    "projectSlug": slug,
                    "status": "failed",
                    "phase": "encode",
                    "quality": "preview",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }
            ),
            "utf-8",
        )
        from app.render.jobs import get_job_manager

        get_job_manager().load_history()

        assert client.get(f"/api/projects/{slug}/shorts/sources").json() == []

    def test_a_deleted_export_is_listed_as_unusable_with_a_reason(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, video = add_source(settings, slug)
        video.unlink()

        source = client.get(f"/api/projects/{slug}/shorts/sources").json()[0]
        assert source["usable"] is False
        assert "no longer on disk" in source["issue"]

    def test_a_changed_export_is_listed_as_unusable(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, video = add_source(settings, slug)
        video.write_bytes(b"m" * 30_000)

        source = client.get(f"/api/projects/{slug}/shorts/sources").json()[0]
        assert source["usable"] is False
        assert "changed" in source["issue"]

    def test_unknown_source_id_is_a_structured_404(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        add_source(settings, slug)
        response = client.get(f"/api/projects/{slug}/shorts/sources/nope/timeline")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "short_source_not_ready"
        assert body["suggestion"]
        assert body["logPath"]


class TestTimeline:
    def test_sections_are_numbered_intro_zero_scenes_then_outro(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        add_source(settings, slug, scene_count=3)

        body = client.get(
            f"/api/projects/{slug}/shorts/sources/render0001/timeline"
        ).json()
        numbers = [(s["kind"], s["number"], s["title"]) for s in body["sections"]]
        assert numbers == [
            ("intro", 0, "Intro"),
            ("scene", 1, "Scene 1"),
            ("scene", 2, "Scene 2"),
            ("scene", 3, "Scene 3"),
            ("outro", 4, "Outro"),
        ]
        assert body["maxSeconds"] == 180.0
        assert body["warnSeconds"] == 60.0
        assert body["recommendedMinSeconds"] == 25.0
        assert body["fps"] > 0

    def test_each_section_reports_its_safe_window_and_transitions(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        add_source(settings, slug)
        body = client.get(
            f"/api/projects/{slug}/shorts/sources/render0001/timeline"
        ).json()

        middle = next(s for s in body["sections"] if s["number"] == 2)
        assert middle["safeStartSeconds"] == pytest.approx(middle["startSeconds"] + 0.5)
        assert middle["safeEndSeconds"] == pytest.approx(middle["endSeconds"] - 0.5)
        assert middle["safeDurationSeconds"] == pytest.approx(9.0)
        assert middle["transitionDurationSeconds"] == 0.5
        assert middle["transitionFromPreviousSeconds"] == 0.5


class TestPathSafety:
    @pytest.mark.parametrize(
        "attempt",
        [
            "../../../../etc/passwd",
            "../../project.json",
            "a/../../b",
            "..%2F..%2Fsecrets.json",
        ],
    )
    def test_export_lookup_refuses_to_escape_the_shorts_folder(
        self, settings, attempt: str
    ) -> None:  # noqa: ANN001
        """The guard itself, independent of any URL normalisation on the way in."""
        from app.errors import AppError
        from app.shorts.service import ShortsService

        slug, _ = make_project(settings)
        with pytest.raises(AppError) as exc:
            ShortsService(settings).export_path(slug, attempt)
        # Either it never resolved inside the folder, or it did and is missing.
        assert exc.value.code.value in {"path_traversal", "short_not_found"}

    def test_export_download_never_serves_a_file_outside_the_project(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, root = make_project(settings)
        marker = root / "project.json"
        response = client.get(f"/api/projects/{slug}/shorts/exports/../../project.json")
        assert response.status_code != 200
        assert marker.read_text("utf-8") not in response.text

    def test_preview_frames_are_confined_to_the_cache(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, root = make_project(settings)
        secret = root / "project.json"
        assert secret.is_file()
        response = client.get(f"/api/projects/{slug}/shorts/frames/../../project.json")
        assert response.status_code in {400, 404}

    def test_delete_rejects_an_unsafe_short_id(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        response = client.delete(f"/api/projects/{slug}/shorts/..%2F..%2Fboom")
        assert response.status_code in {400, 404}


def place_short(
    settings,  # noqa: ANN001
    slug: str,
    *,
    short_id: str,
    cache_key: str,
    source_video: str,
    numbers: list[int],
    duration: float = 30.0,
) -> Path:
    """A finished Short on disk, without having rendered one."""
    paths = ProjectRepository(settings).paths_for(slug)
    paths.shorts_exports.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}-short-{short_id}.mp4"
    video = paths.shorts_exports / filename
    video.write_bytes(b"s" * 15_000)
    video.with_suffix(".log").write_text("log", "utf-8")

    manifest = ShortManifest(
        short_id=short_id,
        project_slug=slug,
        filename=filename,
        cache_key=cache_key,
        source_render_id="render0001",
        source_video=source_video,
        source_sha256="a" * 64,
        duration_seconds=duration,
        size_bytes=video.stat().st_size,
        plan=ShortPlan(
            segments=[
                ShortSegmentPlan(
                    unit_id=f"scene-{n}", number=n, title=f"Scene {n}", kind="scene",
                    start_seconds=0.0, end_seconds=duration, duration_seconds=duration,
                )
                for n in numbers
            ],
            total_duration_seconds=duration,
            cache_key=cache_key,
        ),
        request=ShortRequest(
            source_render_id="render0001",
            segments=[ShortSegmentRequest(unit_id=f"scene-{n}") for n in numbers],
            layout=ShortLayout(),
        ),
    )
    (paths.shorts_exports / f"{video.stem}.json").write_text(
        manifest.model_dump_json(indent=2), "utf-8"
    )
    return video


class TestHistoryAndDeletion:
    def test_lists_finished_shorts_with_their_source_and_sections(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, video = add_source(settings, slug)
        place_short(settings, slug, short_id="aaaa1111", cache_key="aaaa1111",
                    source_video=video.name, numbers=[2, 3])

        body = client.get(f"/api/projects/{slug}/shorts").json()
        assert len(body) == 1
        assert body[0]["shortId"] == "aaaa1111"
        assert body[0]["sectionNumbers"] == [2, 3]
        assert body[0]["sourceVideo"] == video.name
        assert body[0]["url"].endswith(".mp4")
        assert {a["kind"] for a in body[0]["artifacts"]} >= {"video", "manifest", "log"}

    def test_download_serves_the_short(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, video = add_source(settings, slug)
        short = place_short(settings, slug, short_id="bbbb2222", cache_key="bbbb2222",
                            source_video=video.name, numbers=[1])

        response = client.get(f"/api/projects/{slug}/shorts/exports/{short.name}")
        assert response.status_code == 200
        assert len(response.content) == short.stat().st_size

    def test_delete_removes_only_that_short(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, source = add_source(settings, slug)
        keep = place_short(settings, slug, short_id="keep0001", cache_key="keep0001",
                           source_video=source.name, numbers=[1])
        drop = place_short(settings, slug, short_id="drop0001", cache_key="drop0001",
                           source_video=source.name, numbers=[2])
        paths = ProjectRepository(settings).paths_for(slug)

        response = client.delete(f"/api/projects/{slug}/shorts/drop0001")
        assert response.status_code == 200
        assert response.json()["shortId"] == "drop0001"

        assert not drop.is_file()
        assert not (paths.shorts_exports / f"{drop.stem}.json").is_file()
        assert not drop.with_suffix(".log").is_file()

        # Everything else survives: the other Short, the long export, the project.
        assert keep.is_file()
        assert (paths.shorts_exports / f"{keep.stem}.json").is_file()
        assert source.is_file()
        assert (paths.exports / f"{source.stem}-manifest.json").is_file()
        assert paths.project_file.is_file()

    def test_deleting_an_unknown_short_is_a_structured_404(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        response = client.delete(f"/api/projects/{slug}/shorts/ffff9999")
        assert response.status_code == 404
        assert response.json()["code"] == "short_not_found"


class TestIdempotency:
    def test_an_identical_request_reuses_the_finished_short(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        manifest, video = add_source(settings, slug)

        from app.shorts.plan import build_plan
        from tests.shorts_factories import request_for

        request = request_for("scene-2", "scene-3")
        plan = build_plan(manifest, request)
        place_short(settings, slug, short_id=plan.cache_key, cache_key=plan.cache_key,
                    source_video=video.name, numbers=[2, 3])

        response = client.post(
            f"/api/projects/{slug}/shorts",
            json=request.model_dump(mode="json", by_alias=True),
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "completed"
        assert job["cacheReused"] is True
        assert job["shortId"] == plan.cache_key
        assert job["progress"] == 1.0

        # No second file was created.
        paths = ProjectRepository(settings).paths_for(slug)
        assert len(list(paths.shorts_exports.glob("*.mp4"))) == 1

    def test_a_duplicate_of_a_running_short_returns_the_same_job(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        manifest, _ = add_source(settings, slug)

        from app.shorts.jobs import get_short_job_manager
        from app.shorts.models import ShortJob
        from app.shorts.plan import build_plan
        from tests.shorts_factories import request_for

        request = request_for("scene-1")
        plan = build_plan(manifest, request)

        manager = get_short_job_manager()
        manager.ensure_history()
        running = ShortJob(
            id="inflight001",
            project_slug=slug,
            request=request,
            cache_key=plan.cache_key,
            status=JobStatus.RUNNING,
        )
        manager._jobs[running.id] = running  # noqa: SLF001 - simulating an in-flight job

        response = client.post(
            f"/api/projects/{slug}/shorts",
            json=request.model_dump(mode="json", by_alias=True),
        )
        assert response.status_code == 202
        assert response.json()["id"] == "inflight001"
        assert len(manager.list_jobs(project_slug=slug)) == 1

    def test_a_changed_source_does_not_hit_the_cache(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        manifest, video = add_source(settings, slug)

        from app.shorts.plan import build_plan
        from tests.shorts_factories import request_for

        request = request_for("scene-2")
        first_key = build_plan(manifest, request).cache_key

        # Re-render: same selection, different source file.
        second, _ = add_source(settings, slug, render_id="render0002", version=2)
        second_request = request_for("scene-2", render_id="render0002")
        assert build_plan(second, second_request).cache_key != first_key


class TestPreflightErrors:
    def test_a_missing_source_is_reported_as_a_blocking_issue(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        body = client.post(
            f"/api/projects/{slug}/shorts/preflight",
            json={"sourceRenderId": "ghost", "segments": [{"unitId": "scene-1"}]},
        ).json()
        assert body["ready"] is False
        assert body["blockingIssues"]

    def test_a_stale_source_blocks_preflight(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        _, video = add_source(settings, slug)
        video.unlink()

        body = client.post(
            f"/api/projects/{slug}/shorts/preflight",
            json={"sourceRenderId": "render0001", "segments": [{"unitId": "scene-1"}]},
        ).json()
        assert body["ready"] is False
        assert any("no longer" in issue for issue in body["blockingIssues"])

    def test_an_unreadable_mp4_blocks_rather_than_500s(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        """A corrupt export is a blocking issue, not an unhandled ffprobe error."""
        slug, _ = make_project(settings)
        add_source(settings, slug)  # writes a file that is not really an MP4

        response = client.post(
            f"/api/projects/{slug}/shorts/preflight",
            json={"sourceRenderId": "render0001", "segments": [{"unitId": "scene-1"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is False
        assert any("could not be read by FFmpeg" in issue for issue in body["blockingIssues"])

    def test_creating_a_short_with_an_invalid_trim_is_a_422(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = make_project(settings)
        manifest, _ = add_source(settings, slug)
        scene = manifest.entry("scene-2")

        response = client.post(
            f"/api/projects/{slug}/shorts",
            json={
                "sourceRenderId": "render0001",
                "segments": [
                    {
                        "unitId": "scene-2",
                        "startSeconds": scene.start_seconds,
                        "endSeconds": scene.end_seconds,
                    }
                ],
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "short_invalid_trim"
        assert body["suggestion"]
        assert body["details"]
