"""The render job queue.

One render runs at a time. FFmpeg already saturates the CPU, so running two
concurrently makes both slower and can exhaust memory on a laptop; queued jobs
wait their turn.

Every state change is written to disk immediately. That is what makes the two
guarantees here possible: the render history survives a restart, and a job that
was killed mid-render is reported as *interrupted* rather than sitting forever
in "running".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ConflictError, ErrorCode, NotFoundError
from app.models.enums import JobPhase, JobStatus, QualityPreset
from app.models.jobs import JobArtifact, JobEvent, RenderJob
from app.render.ffmpeg import CancelledRender
from app.render.pipeline import RenderPipeline
from app.render.slot import render_slot
from app.storage.repository import ProjectRepository

logger = logging.getLogger("evb.jobs")

#: Jobs older than this are pruned from the history on startup.
HISTORY_RETENTION_DAYS = 60
MAX_HISTORY_ENTRIES = 200


@dataclass
class _RunningJob:
    job: RenderJob
    cancel_event: asyncio.Event
    task: asyncio.Task | None = None
    subscribers: list[asyncio.Queue[JobEvent]] = field(default_factory=list)


class JobManager:
    """Owns the queue, the worker loop and the on-disk history."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._jobs: dict[str, RenderJob] = {}
        self._running: dict[str, _RunningJob] = {}
        self._worker: asyncio.Task | None = None
        # The queue and lock are created lazily, inside whichever event loop is
        # actually running. Building them in __init__ binds them to the loop
        # that happened to exist at import time, which breaks as soon as the
        # loop is replaced — on a uvicorn reload, or between tests.
        self._queue: asyncio.Queue[str] | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> None:
        """Attach the queue and lock to the running loop, rebuilding on change."""
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._queue is not None and self._lock is not None:
            return

        pending = []
        if self._queue is not None:
            # Carry any still-queued work across to the new loop's queue.
            while not self._queue.empty():
                pending.append(self._queue.get_nowait())

        self._loop = loop
        self._queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        for job_id in pending:
            self._queue.put_nowait(job_id)

    # --- persistence ------------------------------------------------------

    @property
    def _history_dir(self) -> Path:
        return self.settings.data_dir / "jobs"

    def _job_file(self, job_id: str) -> Path:
        return self._history_dir / f"{job_id}.json"

    def _persist(self, job: RenderJob) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._job_file(job.id).with_suffix(".json.tmp")
        tmp.write_text(job.model_dump_json(indent=2), "utf-8")
        tmp.replace(self._job_file(job.id))

    def load_history(self) -> None:
        """Read every stored job, marking any that were killed mid-render."""
        self._history_dir.mkdir(parents=True, exist_ok=True)
        interrupted = 0

        for path in sorted(self._history_dir.glob("*.json")):
            try:
                job = RenderJob.model_validate(json.loads(path.read_text("utf-8")))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("skipping unreadable job file %s: %s", path.name, exc)
                continue

            # A job recorded as active but with no live process was killed —
            # the app crashed, or the machine restarted mid-render.
            if job.is_active and not _process_alive(job.pid):
                job.status = JobStatus.INTERRUPTED
                job.message = (
                    "Yarıda kaldı — video oluşturulurken uygulama kapandı."
                )
                job.finished_at = job.finished_at or datetime.now(timezone.utc)
                job.error_code = ErrorCode.RENDER_FAILED.value
                job.error_message = "Video oluşturma tamamlanamadan yarıda kesildi."
                job.error_suggestion = (
                    "Yeniden başlatın. Hazır olan sahneler tekrar kullanılacağı için bu sefer "
                    "çok daha hızlı bitecek."
                )
                self._persist(job)
                interrupted += 1

            self._jobs[job.id] = job

        if interrupted:
            logger.warning("marked %d interrupted render(s) from a previous session", interrupted)
        self._prune_history()

    def _prune_history(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
        finished = sorted(
            (j for j in self._jobs.values() if j.is_terminal),
            key=lambda j: j.created_at,
            reverse=True,
        )
        for position, job in enumerate(finished):
            too_old = job.created_at < cutoff
            too_many = position >= MAX_HISTORY_ENTRIES
            if too_old or too_many:
                self._jobs.pop(job.id, None)
                self._job_file(job.id).unlink(missing_ok=True)

    def cleanup_abandoned_temp(self) -> int:
        """Remove scratch directories left behind by interrupted renders.

        Only touches the app's own temp directory, and only entries older than
        the configured retention.
        """
        temp_dir = self.settings.temp_dir
        if not temp_dir.is_dir():
            return 0

        cutoff = time.time() - self.settings.mutable.temp_retention_days * 86_400
        removed = 0
        for entry in temp_dir.iterdir():
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                if entry.is_dir():
                    import shutil

                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:  # pragma: no cover - permissions edge case
                logger.warning("could not remove temp entry %s: %s", entry, exc)
        if removed:
            logger.info("cleaned %d abandoned temp entries", removed)
        return removed

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        self._bind_loop()
        self.load_history()
        self.cleanup_abandoned_temp()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        for running in list(self._running.values()):
            running.cancel_event.set()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await self._worker
            self._worker = None
        # Release the loop binding so a later start() rebuilds cleanly.
        self._loop = None

    # --- public API -------------------------------------------------------

    def list_jobs(self, *, project_slug: str | None = None, limit: int = 50) -> list[RenderJob]:
        jobs = [
            job for job in self._jobs.values()
            if project_slug is None or job.project_slug == project_slug
        ]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get(self, job_id: str) -> RenderJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                ErrorCode.JOB_NOT_FOUND,
                f"'{job_id}' numaralı bir işlem bulunamadı.",
                suggestion="Geçmişi yenileyin; eski kayıtlar bir süre sonra silinir.",
            )
        return job

    def active_job(self) -> RenderJob | None:
        return next((j for j in self._jobs.values() if j.is_active), None)

    async def submit(
        self, project_slug: str, *, quality: QualityPreset | None = None
    ) -> RenderJob:
        """Queue a render. Rejects a second render of the same project."""
        self._bind_loop()
        assert self._lock is not None and self._queue is not None

        async with self._lock:
            existing = next(
                (j for j in self._jobs.values()
                 if j.project_slug == project_slug and j.is_active),
                None,
            )
            if existing is not None:
                raise ConflictError(
                    ErrorCode.RENDER_FAILED,
                    f"'{project_slug}' projesi için zaten bir video oluşturuluyor.",
                    details=f"işlem numarası {existing.id}",
                    suggestion="Bitmesini bekleyin ya da onu iptal edip yeniden başlatın.",
                )

            repository = ProjectRepository(self.settings)
            project = repository.load(project_slug)  # 404s early if it is gone

            job = RenderJob(
                project_slug=project_slug,
                quality=quality or project.export.quality,
                pid=os.getpid(),
            )
            self._jobs[job.id] = job
            self._persist(job)
            await self._queue.put(job.id)

        logger.info("queued render job %s for %s", job.id, project_slug)
        await self.start()
        return job

    async def cancel(self, job_id: str) -> RenderJob:
        job = self.get(job_id)
        if job.is_terminal:
            raise ConflictError(
                ErrorCode.RENDER_FAILED,
                "Bu işlem zaten tamamlanmış.",
                suggestion="Bunun yerine yeni bir video oluşturun.",
            )

        running = self._running.get(job_id)
        if running is not None:
            running.cancel_event.set()
            logger.info("cancellation requested for running job %s", job_id)
        else:
            # Still queued: mark it now so the worker skips it.
            self._finalize(job, JobStatus.CANCELLED, "Başlamadan iptal edildi.")
        return job

    async def retry(self, job_id: str) -> RenderJob:
        """Queue a fresh render of the same project as a failed job."""
        job = self.get(job_id)
        if job.is_active:
            raise ConflictError(
                ErrorCode.RENDER_FAILED,
                "Bu video hâlâ oluşturuluyor.",
                suggestion="Önce iptal edin ya da bitmesini bekleyin.",
            )
        return await self.submit(job.project_slug, quality=job.quality)

    async def subscribe(self, job_id: str) -> AsyncIterator[JobEvent]:
        """Yield progress events for a job until it reaches a terminal state."""
        job = self.get(job_id)

        # A finished job still gets one event, so a late subscriber is not left
        # waiting forever for something that already happened.
        if job.is_terminal:
            yield _event_for(job)
            return

        queue: asyncio.Queue[JobEvent] = asyncio.Queue()
        running = self._running.get(job_id)
        if running is not None:
            running.subscribers.append(queue)
        else:
            # Queued but not started: poll until the worker picks it up.
            while job.is_active and job_id not in self._running:
                yield _event_for(job)
                await asyncio.sleep(0.5)
                job = self.get(job_id)
            running = self._running.get(job_id)
            if running is None:
                yield _event_for(job)
                return
            running.subscribers.append(queue)

        try:
            yield _event_for(job)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies from closing an idle connection.
                    event = _event_for(self.get(job_id))
                yield event
                if event.status in {
                    JobStatus.COMPLETED, JobStatus.FAILED,
                    JobStatus.CANCELLED, JobStatus.INTERRUPTED,
                }:
                    return
        finally:
            with contextlib.suppress(ValueError):
                running.subscribers.remove(queue)

    # --- worker -----------------------------------------------------------

    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.is_terminal:
                continue
            try:
                # Shared with the Shorts queue: only one CPU-heavy FFmpeg job
                # runs at a time, whichever kind it is.
                async with render_slot(label=f"render job {job_id}"):
                    if job.is_terminal:  # cancelled while it waited for the slot
                        continue
                    await self._run_job(job)
            except Exception:  # noqa: BLE001 - the worker must survive any job
                logger.exception("job %s crashed the worker loop", job_id)

    async def _run_job(self, job: RenderJob) -> None:
        cancel_event = asyncio.Event()
        running = _RunningJob(job=job, cancel_event=cancel_event)
        self._running[job.id] = running

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        job.pid = os.getpid()
        job.message = "Başlıyor"
        self._persist(job)
        self._broadcast(running)

        repository = ProjectRepository(self.settings)

        try:
            project = repository.load(job.project_slug)
            paths = repository.paths_for(job.project_slug)

            def on_progress(phase: JobPhase, overall: float, message: str) -> None:
                job.phase = phase
                job.progress = overall
                job.message = message
                self._broadcast(running)

            pipeline = RenderPipeline(
                project, paths,
                settings=self.settings,
                on_progress=on_progress,
                cancel_event=cancel_event,
                quality=job.quality,
                job_id=job.id,
            )
            result = await pipeline.run()

            job.output_file = result.artifacts.video.name
            job.total_duration_seconds = result.timeline.total_duration_seconds
            job.scenes_rendered = result.rendered_clips
            job.scenes_reused = result.reused_clips
            job.warnings = list(result.warnings)
            job.artifacts = _collect_artifacts(job.project_slug, result)
            job.log_file = (
                result.artifacts.render_log.name if result.artifacts.render_log else None
            )
            self._finalize(job, JobStatus.COMPLETED, "Video hazır.", running)

        except CancelledRender:
            logger.info("job %s cancelled", job.id)
            self._finalize(job, JobStatus.CANCELLED, "İptal edildi.", running)

        except AppError as exc:
            logger.warning("job %s failed: %s", job.id, exc)
            job.error_code = exc.code.value
            job.error_message = exc.message
            job.error_details = exc.details
            job.error_suggestion = exc.suggestion
            self._finalize(job, JobStatus.FAILED, exc.message, running)

        except Exception as exc:  # noqa: BLE001
            import traceback

            logger.exception("job %s failed unexpectedly", job.id)
            job.error_code = ErrorCode.INTERNAL.value
            job.error_message = "Beklenmedik bir hata video oluşturmayı durdurdu."
            job.error_details = traceback.format_exc()[-4000:]
            job.error_suggestion = (
                "Bu bir yazılım hatası. Ayrıntılar yukarıda ve kayıt dosyasında yer alıyor."
            )
            self._finalize(job, JobStatus.FAILED, str(exc), running)

        finally:
            self._running.pop(job.id, None)

    def _finalize(
        self,
        job: RenderJob,
        status: JobStatus,
        message: str,
        running: _RunningJob | None = None,
    ) -> None:
        job.status = status
        job.message = message
        job.finished_at = datetime.now(timezone.utc)
        job.progress = 1.0 if status is JobStatus.COMPLETED else job.progress
        job.pid = None
        self._persist(job)
        if running is not None:
            self._broadcast(running)

    def _broadcast(self, running: _RunningJob) -> None:
        event = _event_for(running.job)
        for queue in list(running.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        # Persist periodically so a crash loses at most the last update.
        if running.job.is_terminal or int(running.job.progress * 100) % 5 == 0:
            self._persist(running.job)


def _event_for(job: RenderJob) -> JobEvent:
    return JobEvent(
        job_id=job.id,
        status=job.status,
        phase=job.phase,
        progress=job.progress,
        message=job.message,
        elapsed_seconds=round(job.elapsed_seconds, 2),
        estimated_remaining_seconds=(
            round(job.estimated_remaining_seconds, 1)
            if job.estimated_remaining_seconds is not None
            else None
        ),
        error_code=job.error_code,
        error_message=job.error_message,
        error_suggestion=job.error_suggestion,
    )


def _collect_artifacts(slug: str, result) -> list[JobArtifact]:  # noqa: ANN001
    artifacts: list[JobArtifact] = []

    def add(kind: str, path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        artifacts.append(
            JobArtifact(
                kind=kind,
                filename=path.name,
                size_bytes=path.stat().st_size,
                url=f"/api/projects/{slug}/exports/{path.name}",
            )
        )

    add("video", result.artifacts.video)
    add("subtitles", result.artifacts.subtitles)
    add("narration", result.artifacts.narration_audio)
    add("description", result.artifacts.description)
    add("thumbnail", result.artifacts.thumbnail_prompt)
    add("project", result.artifacts.project_snapshot)
    add("log", result.artifacts.render_log)
    add("report", result.artifacts.report)
    add("manifest", result.artifacts.manifest)
    return artifacts


def _process_alive(pid: int | None) -> bool:
    """Whether a pid is still running. Used to detect interrupted renders."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists but belongs to another user; treat it as alive.
        return True
    return True


#: One manager per process.
_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager(get_settings())
    return _manager


def reset_job_manager() -> None:
    """Drop the singleton. Used by tests to isolate state."""
    global _manager
    _manager = None
