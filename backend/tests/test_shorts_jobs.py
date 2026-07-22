"""The Shorts queue: persistence, crash recovery, cancellation and isolation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import AppError
from app.models.enums import JobStatus
from app.shorts.jobs import ShortJobManager
from app.shorts.models import ShortJob, ShortRequest, ShortSegmentRequest
from app.storage.repository import ProjectRepository
from tests.shorts_factories import build_entries, make_manifest, request_for, write_manifest


@pytest.fixture(autouse=True)
def fresh_slot():  # noqa: ANN201
    from app.render.slot import reset_render_slot

    reset_render_slot()
    yield
    reset_render_slot()


@pytest.fixture
def manager(settings) -> ShortJobManager:  # noqa: ANN001
    return ShortJobManager(settings)


@pytest.fixture
def project(settings):  # noqa: ANN001, ANN201
    repository = ProjectRepository(settings)
    created = repository.create("Shorts Jobs")
    paths = repository.paths_for(created.slug)
    paths.ensure()
    video = paths.exports / f"{created.slug}_v01.mp4"
    video.write_bytes(b"v" * 12_000)
    entries, total = build_entries(scene_count=3)
    manifest = make_manifest(video, slug=created.slug, entries=entries, total=total)
    write_manifest(manifest, video)
    return created.slug, manifest


class TestJobModel:
    def test_starts_queued_and_active(self) -> None:
        job = ShortJob(project_slug="x", request=ShortRequest(source_render_id="r"))
        assert job.status is JobStatus.QUEUED
        assert job.is_active and not job.is_terminal

    @pytest.mark.parametrize(
        "status",
        [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED],
    )
    def test_terminal_states(self, status: JobStatus) -> None:
        job = ShortJob(
            project_slug="x", request=ShortRequest(source_render_id="r"), status=status
        )
        assert job.is_terminal and not job.is_active

    def test_no_estimate_before_meaningful_progress(self) -> None:
        job = ShortJob(
            project_slug="x", request=ShortRequest(source_render_id="r"),
            status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc), progress=0.01,
        )
        assert job.estimated_remaining_seconds is None

    def test_estimate_from_elapsed_and_progress(self) -> None:
        job = ShortJob(
            project_slug="x", request=ShortRequest(source_render_id="r"),
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=40),
            progress=0.5,
        )
        assert 35 < job.estimated_remaining_seconds < 45


class TestPersistence:
    def test_a_submitted_job_is_written_to_disk_immediately(
        self, manager: ShortJobManager, project, settings
    ) -> None:  # noqa: ANN001
        slug, _ = project

        async def run() -> ShortJob:
            job = await manager.submit(slug, request_for("scene-1"))
            await manager.stop()
            return job

        job = asyncio.run(run())
        path = settings.data_dir / "short-jobs" / f"{job.id}.json"
        assert path.is_file()
        stored = json.loads(path.read_text("utf-8"))
        assert stored["id"] == job.id
        assert stored["cacheKey"] == job.cache_key
        assert stored["request"]["segments"][0]["unitId"] == "scene-1"

    def test_history_survives_a_restart(
        self, manager: ShortJobManager, project, settings
    ) -> None:  # noqa: ANN001
        slug, _ = project

        async def run() -> str:
            job = await manager.submit(slug, request_for("scene-1"))
            await manager.stop()
            return job.id

        job_id = asyncio.run(run())

        revived = ShortJobManager(settings)
        revived.load_history()
        assert revived.get(job_id).project_slug == slug

    def test_a_job_killed_mid_build_is_marked_interrupted(
        self, settings
    ) -> None:  # noqa: ANN001
        history = settings.data_dir / "short-jobs"
        history.mkdir(parents=True, exist_ok=True)
        job = ShortJob(
            id="killed01",
            project_slug="ghost",
            request=ShortRequest(
                source_render_id="r", segments=[ShortSegmentRequest(unit_id="scene-1")]
            ),
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            pid=999_999,  # long gone
        )
        (history / "killed01.json").write_text(job.model_dump_json(indent=2), "utf-8")

        manager = ShortJobManager(settings)
        manager.load_history()
        recovered = manager.get("killed01")

        assert recovered.status is JobStatus.INTERRUPTED
        assert "Yarıda kaldı" in recovered.message
        assert recovered.error_suggestion
        # The stored copy was updated too, so a second restart stays consistent.
        stored = json.loads((history / "killed01.json").read_text("utf-8"))
        assert stored["status"] == "interrupted"

    def test_a_live_running_job_is_left_alone(self, settings) -> None:  # noqa: ANN001
        import os

        history = settings.data_dir / "short-jobs"
        history.mkdir(parents=True, exist_ok=True)
        job = ShortJob(
            id="alive001",
            project_slug="ghost",
            request=ShortRequest(source_render_id="r"),
            status=JobStatus.RUNNING,
            pid=os.getpid(),
        )
        (history / "alive001.json").write_text(job.model_dump_json(indent=2), "utf-8")

        manager = ShortJobManager(settings)
        manager.load_history()
        assert manager.get("alive001").status is JobStatus.RUNNING

    def test_old_history_is_pruned(self, settings) -> None:  # noqa: ANN001
        history = settings.data_dir / "short-jobs"
        history.mkdir(parents=True, exist_ok=True)
        stale = ShortJob(
            id="ancient1",
            project_slug="ghost",
            request=ShortRequest(source_render_id="r"),
            status=JobStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
        (history / "ancient1.json").write_text(stale.model_dump_json(indent=2), "utf-8")

        manager = ShortJobManager(settings)
        manager.load_history()
        with pytest.raises(AppError):
            manager.get("ancient1")
        assert not (history / "ancient1.json").is_file()


class TestQueueBehaviour:
    def test_cancelling_a_queued_job_finalizes_it_without_running(
        self, manager: ShortJobManager, project
    ) -> None:  # noqa: ANN001
        slug, _ = project

        async def run() -> ShortJob:
            manager._bind_loop()  # noqa: SLF001
            manager.ensure_history()
            job = await manager.submit(slug, request_for("scene-1"))
            # Stop the worker before it can pick the job up, then cancel.
            await manager.stop()
            return await manager.cancel(job.id)

        cancelled = asyncio.run(run())
        assert cancelled.status is JobStatus.CANCELLED
        assert "Başlamadan iptal" in cancelled.message

    def test_cancelling_a_finished_job_is_a_conflict(
        self, manager: ShortJobManager, project
    ) -> None:  # noqa: ANN001
        slug, _ = project
        job = ShortJob(
            project_slug=slug,
            request=request_for("scene-1"),
            status=JobStatus.COMPLETED,
        )
        manager._jobs[job.id] = job  # noqa: SLF001

        async def run() -> None:
            await manager.cancel(job.id)

        with pytest.raises(AppError) as exc:
            asyncio.run(run())
        assert exc.value.http_status == 409

    def test_an_invalid_request_never_creates_a_job(
        self, manager: ShortJobManager, project
    ) -> None:  # noqa: ANN001
        slug, manifest = project
        scene = manifest.entry("scene-2")

        async def run() -> None:
            await manager.submit(
                slug,
                request_for(
                    "scene-2", trims={"scene-2": (scene.start_seconds, scene.end_seconds)}
                ),
            )

        with pytest.raises(AppError):
            asyncio.run(run())
        assert manager.list_jobs(project_slug=slug) == []

    def test_active_job_is_scoped_to_a_project(
        self, manager: ShortJobManager, project
    ) -> None:  # noqa: ANN001
        slug, _ = project
        running = ShortJob(
            project_slug=slug, request=request_for("scene-1"), status=JobStatus.RUNNING
        )
        manager._jobs[running.id] = running  # noqa: SLF001

        assert manager.active_job(project_slug=slug) is running
        assert manager.active_job(project_slug="another-project") is None


class TestSharedRenderSlot:
    def test_only_one_heavy_job_holds_the_slot_at_a_time(self) -> None:
        from app.render.slot import render_slot

        order: list[str] = []

        async def worker(name: str, hold: float) -> None:
            async with render_slot(label=name):
                order.append(f"{name}:start")
                await asyncio.sleep(hold)
                order.append(f"{name}:end")

        async def run() -> None:
            await asyncio.gather(worker("render", 0.05), worker("short", 0.05))

        asyncio.run(run())

        # Whichever went first, it finished before the other started.
        assert order[1].endswith(":end")
        assert order[0].split(":")[0] == order[1].split(":")[0]

    def test_the_slot_rebinds_across_event_loops(self) -> None:
        from app.render.slot import render_slot

        async def once() -> None:
            async with render_slot():
                pass

        asyncio.run(once())
        asyncio.run(once())  # a second loop must not deadlock on the first's semaphore
