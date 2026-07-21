"""Render job queue: persistence, cancellation, retry and crash recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import JobStatus, QualityPreset
from app.models.jobs import RenderJob
from app.render.jobs import JobManager, _process_alive
from app.storage.repository import ProjectRepository
from app.models.project import Scene


@pytest.fixture
def manager(settings) -> JobManager:  # noqa: ANN001
    return JobManager(settings)


@pytest.fixture
def project_slug(settings) -> str:  # noqa: ANN001
    repository = ProjectRepository(settings)
    project = repository.create("Job Test")
    project.scenes = [Scene(title="One", manual_duration_seconds=2.0)]
    repository.save(project)
    return project.slug


class TestJobModel:
    def test_starts_queued_and_not_terminal(self) -> None:
        job = RenderJob(project_slug="x")
        assert job.status is JobStatus.QUEUED
        assert job.is_active and not job.is_terminal

    @pytest.mark.parametrize(
        "status",
        [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED],
    )
    def test_terminal_states(self, status: JobStatus) -> None:
        job = RenderJob(project_slug="x", status=status)
        assert job.is_terminal and not job.is_active

    def test_elapsed_time_tracks_the_run(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=30)
        job = RenderJob(project_slug="x", status=JobStatus.RUNNING, started_at=started)
        assert 29 < job.elapsed_seconds < 32

    def test_no_estimate_before_meaningful_progress(self) -> None:
        job = RenderJob(
            project_slug="x", status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc), progress=0.01,
        )
        assert job.estimated_remaining_seconds is None

    def test_estimate_from_elapsed_and_progress(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=60)
        job = RenderJob(
            project_slug="x", status=JobStatus.RUNNING, started_at=started, progress=0.25,
        )
        # 60s got us to 25%, so roughly 180s remain.
        assert 170 < job.estimated_remaining_seconds < 190

    def test_finished_job_has_no_estimate(self) -> None:
        job = RenderJob(project_slug="x", status=JobStatus.COMPLETED, progress=1.0)
        assert job.estimated_remaining_seconds is None


class TestPersistence:
    async def test_submitted_job_is_written_to_disk(
        self, manager: JobManager, project_slug: str, settings
    ) -> None:  # noqa: ANN001
        job = await manager.submit(project_slug)
        path = settings.data_dir / "jobs" / f"{job.id}.json"
        assert path.is_file()
        assert json.loads(path.read_text("utf-8"))["projectSlug"] == project_slug
        await manager.stop()

    async def test_history_survives_a_restart(
        self, manager: JobManager, project_slug: str, settings
    ) -> None:  # noqa: ANN001
        job = await manager.submit(project_slug)
        await manager.stop()

        fresh = JobManager(settings)
        fresh.load_history()
        assert fresh.get(job.id).project_slug == project_slug

    def test_unreadable_job_file_is_skipped_not_fatal(
        self, manager: JobManager, settings
    ) -> None:  # noqa: ANN001
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "broken.json").write_text("{ not json", "utf-8")
        (jobs_dir / "good.json").write_text(
            RenderJob(project_slug="ok").model_dump_json(), "utf-8"
        )

        manager.load_history()
        assert len(manager.list_jobs()) == 1


class TestCrashRecovery:
    def test_a_job_from_a_dead_process_is_marked_interrupted(
        self, manager: JobManager, settings
    ) -> None:  # noqa: ANN001
        """The guarantee: a killed render never sits in 'running' forever."""
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        stale = RenderJob(
            project_slug="dodo",
            status=JobStatus.RUNNING,
            progress=0.42,
            started_at=datetime.now(timezone.utc),
            pid=999_999,  # certainly not running
        )
        (jobs_dir / f"{stale.id}.json").write_text(stale.model_dump_json(), "utf-8")

        manager.load_history()
        recovered = manager.get(stale.id)

        assert recovered.status is JobStatus.INTERRUPTED
        assert recovered.finished_at is not None
        assert "Interrupted" in recovered.message
        assert recovered.error_suggestion
        assert "faster" in recovered.error_suggestion  # cached clips will be reused

    def test_the_change_is_written_back(self, manager: JobManager, settings) -> None:  # noqa: ANN001
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        stale = RenderJob(project_slug="dodo", status=JobStatus.RUNNING, pid=999_999)
        path = jobs_dir / f"{stale.id}.json"
        path.write_text(stale.model_dump_json(), "utf-8")

        manager.load_history()
        assert json.loads(path.read_text("utf-8"))["status"] == "interrupted"

    def test_a_job_from_this_live_process_is_left_alone(
        self, manager: JobManager, settings
    ) -> None:  # noqa: ANN001
        import os

        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        live = RenderJob(project_slug="dodo", status=JobStatus.RUNNING, pid=os.getpid())
        (jobs_dir / f"{live.id}.json").write_text(live.model_dump_json(), "utf-8")

        manager.load_history()
        assert manager.get(live.id).status is JobStatus.RUNNING

    def test_completed_jobs_are_untouched(self, manager: JobManager, settings) -> None:  # noqa: ANN001
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        done = RenderJob(project_slug="dodo", status=JobStatus.COMPLETED, pid=999_999)
        (jobs_dir / f"{done.id}.json").write_text(done.model_dump_json(), "utf-8")

        manager.load_history()
        assert manager.get(done.id).status is JobStatus.COMPLETED

    def test_process_alive_detection(self) -> None:
        import os

        assert _process_alive(os.getpid()) is True
        assert _process_alive(999_999) is False
        assert _process_alive(None) is False


class TestQueueRules:
    async def test_a_second_render_of_the_same_project_is_refused(
        self, manager: JobManager, project_slug: str
    ) -> None:
        await manager.submit(project_slug)
        with pytest.raises(AppError) as exc_info:
            await manager.submit(project_slug)
        assert exc_info.value.http_status == 409
        assert "already" in exc_info.value.message
        assert "cancel" in exc_info.value.suggestion
        await manager.stop()

    async def test_rendering_a_missing_project_fails_early(self, manager: JobManager) -> None:
        with pytest.raises(AppError) as exc_info:
            await manager.submit("does-not-exist")
        assert exc_info.value.code is ErrorCode.PROJECT_NOT_FOUND

    async def test_quality_override_is_recorded(
        self, manager: JobManager, project_slug: str
    ) -> None:
        job = await manager.submit(project_slug, quality=QualityPreset.PREVIEW)
        assert job.quality is QualityPreset.PREVIEW
        await manager.stop()

    def test_unknown_job_id_is_a_clear_404(self, manager: JobManager) -> None:
        with pytest.raises(AppError) as exc_info:
            manager.get("nope")
        assert exc_info.value.code is ErrorCode.JOB_NOT_FOUND
        assert exc_info.value.http_status == 404


class TestCancellation:
    async def test_a_queued_job_cancels_immediately(
        self, manager: JobManager, project_slug: str
    ) -> None:
        job = RenderJob(project_slug=project_slug)
        manager._jobs[job.id] = job  # noqa: SLF001 - queued but never started

        cancelled = await manager.cancel(job.id)
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.finished_at is not None

    async def test_cancelling_a_finished_job_is_refused(self, manager: JobManager) -> None:
        job = RenderJob(project_slug="x", status=JobStatus.COMPLETED)
        manager._jobs[job.id] = job  # noqa: SLF001

        with pytest.raises(AppError) as exc_info:
            await manager.cancel(job.id)
        assert exc_info.value.http_status == 409

    async def test_retry_queues_a_new_job_for_the_same_project(
        self, manager: JobManager, project_slug: str
    ) -> None:
        failed = RenderJob(
            project_slug=project_slug, status=JobStatus.FAILED, quality=QualityPreset.HIGH
        )
        manager._jobs[failed.id] = failed  # noqa: SLF001

        retried = await manager.retry(failed.id)
        assert retried.id != failed.id
        assert retried.project_slug == project_slug
        assert retried.quality is QualityPreset.HIGH
        assert retried.status is JobStatus.QUEUED
        await manager.stop()

    async def test_retrying_a_running_job_is_refused(
        self, manager: JobManager, project_slug: str
    ) -> None:
        running = RenderJob(project_slug=project_slug, status=JobStatus.RUNNING)
        manager._jobs[running.id] = running  # noqa: SLF001
        with pytest.raises(AppError) as exc_info:
            await manager.retry(running.id)
        assert exc_info.value.http_status == 409


class TestHistory:
    def test_listed_newest_first(self, manager: JobManager) -> None:
        older = RenderJob(
            project_slug="a", status=JobStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        newer = RenderJob(project_slug="b", status=JobStatus.COMPLETED)
        manager._jobs = {older.id: older, newer.id: newer}  # noqa: SLF001

        assert [j.project_slug for j in manager.list_jobs()] == ["b", "a"]

    def test_filtered_by_project(self, manager: JobManager) -> None:
        for slug in ("a", "a", "b"):
            job = RenderJob(project_slug=slug, status=JobStatus.COMPLETED)
            manager._jobs[job.id] = job  # noqa: SLF001

        assert len(manager.list_jobs(project_slug="a")) == 2
        assert len(manager.list_jobs(project_slug="b")) == 1

    def test_old_entries_are_pruned(self, manager: JobManager, settings) -> None:  # noqa: ANN001
        jobs_dir = settings.data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        ancient = RenderJob(
            project_slug="old", status=JobStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=120),
        )
        recent = RenderJob(project_slug="new", status=JobStatus.COMPLETED)
        for job in (ancient, recent):
            (jobs_dir / f"{job.id}.json").write_text(job.model_dump_json(), "utf-8")

        manager.load_history()
        slugs = {j.project_slug for j in manager.list_jobs()}
        assert "new" in slugs
        assert "old" not in slugs

    def test_active_job_lookup(self, manager: JobManager) -> None:
        assert manager.active_job() is None
        running = RenderJob(project_slug="x", status=JobStatus.RUNNING)
        manager._jobs[running.id] = running  # noqa: SLF001
        assert manager.active_job() is running


class TestTempCleanup:
    def test_removes_only_old_entries(self, manager: JobManager, settings) -> None:  # noqa: ANN001
        import os
        import time

        temp = settings.temp_dir
        temp.mkdir(parents=True, exist_ok=True)

        fresh = temp / "fresh-render"
        fresh.mkdir()
        (fresh / "clip.mp4").write_bytes(b"x")

        old = temp / "abandoned-render"
        old.mkdir()
        (old / "clip.mp4").write_bytes(b"x")
        ancient = time.time() - 30 * 86_400
        os.utime(old, (ancient, ancient))

        removed = manager.cleanup_abandoned_temp()

        assert removed >= 1
        assert fresh.exists(), "a recent render's scratch must survive"
        assert not old.exists()

    def test_missing_temp_dir_is_not_an_error(self, manager: JobManager, settings) -> None:  # noqa: ANN001
        import shutil

        shutil.rmtree(settings.temp_dir, ignore_errors=True)
        assert manager.cleanup_abandoned_temp() == 0


class TestEvents:
    async def test_a_finished_job_still_yields_one_event(self, manager: JobManager) -> None:
        """A late subscriber must not wait forever for something already done."""
        job = RenderJob(project_slug="x", status=JobStatus.COMPLETED, progress=1.0)
        manager._jobs[job.id] = job  # noqa: SLF001

        events = [event async for event in manager.subscribe(job.id)]

        assert len(events) == 1
        assert events[0].status is JobStatus.COMPLETED
        assert events[0].progress == 1.0

    async def test_a_failed_job_reports_the_error_in_its_event(
        self, manager: JobManager
    ) -> None:
        job = RenderJob(
            project_slug="x",
            status=JobStatus.FAILED,
            error_code="missing_image",
            error_message="Scene 2 has no image.",
            error_suggestion="Upload an image for scene 2.",
        )
        manager._jobs[job.id] = job  # noqa: SLF001

        events = [event async for event in manager.subscribe(job.id)]
        assert events[0].error_code == "missing_image"
        assert events[0].error_suggestion == "Upload an image for scene 2."


class TestEventLoopBinding:
    """A singleton manager must survive the event loop being replaced.

    asyncio primitives bind to the loop that created them. Building the queue in
    __init__ meant the manager broke the moment the loop changed — on a uvicorn
    reload, or between two TestClient instances.
    """

    def test_survives_a_new_event_loop(self, settings) -> None:  # noqa: ANN001
        manager = JobManager(settings)

        async def cycle() -> int:
            await manager.start()
            await manager.stop()
            return len(manager.list_jobs())

        # Two separate loops, as two TestClient sessions would create.
        asyncio.run(cycle())
        asyncio.run(cycle())

    def test_queued_work_carries_across_loops(self, settings, project_slug: str) -> None:  # noqa: ANN001
        manager = JobManager(settings)

        async def enqueue() -> str:
            await manager.start()
            job = await manager.submit(project_slug)
            # Stop before the worker can pick it up.
            await manager.stop()
            return job.id

        job_id = asyncio.run(enqueue())

        async def restart() -> None:
            manager._bind_loop()  # noqa: SLF001 - the behaviour under test
            assert manager._queue is not None  # noqa: SLF001

        asyncio.run(restart())
        assert manager.get(job_id).project_slug == project_slug
