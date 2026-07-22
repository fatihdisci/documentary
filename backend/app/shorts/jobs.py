"""The Shorts job queue.

Same contract as the long-render queue — jobs are persisted on every state
change, a killed job is reported as *interrupted* rather than sitting in
"running", and progress is delivered over SSE with a polling fallback — but a
separate queue, a separate history and a separate on-disk shape, because a Short
is not a render and the long ``RenderJob`` schema must keep parsing.

The two queues share the process-wide render slot (``render/slot.py``), so one
CPU-heavy FFmpeg job runs at a time whichever kind it is.

Idempotency lives here. A Short is content-addressed by its cache key, so
submitting the same request twice never starts a second job: if the Short is
already on disk the job completes immediately as a reuse, and if one is already
running the caller gets that job back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ConflictError, ErrorCode, NotFoundError, ValidationError
from app.models.enums import JobStatus
from app.render.ffmpeg import CancelledRender
from app.render.jobs import _process_alive
from app.render.slot import render_slot
from app.shorts.cues import CueSidecar
from app.shorts.manifest import RenderManifest as ShortRenderManifest
from app.shorts.models import ShortJob, ShortJobEvent, ShortPhase, ShortRequest
from app.shorts.pipeline import ShortsPipeline, artifacts_for
from app.shorts.plan import build_plan
from app.shorts.service import ShortsService

logger = logging.getLogger("evb.shorts.jobs")

HISTORY_RETENTION_DAYS = 60
MAX_HISTORY_ENTRIES = 200


@dataclass
class _RunningShort:
    job: ShortJob
    cancel_event: asyncio.Event
    subscribers: list[asyncio.Queue[ShortJobEvent]] = field(default_factory=list)


class ShortJobManager:
    """Owns the Shorts queue, worker loop and on-disk history."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.service = ShortsService(self.settings)
        self._jobs: dict[str, ShortJob] = {}
        self._running: dict[str, _RunningShort] = {}
        self._worker: asyncio.Task | None = None
        self._history_loaded = False
        # Bound lazily to whichever loop is actually running, for the same
        # reason the render queue does it: a reload or the next test replaces it.
        self._queue: asyncio.Queue[str] | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._queue is not None and self._lock is not None:
            return

        pending: list[str] = []
        if self._queue is not None:
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
        return self.settings.data_dir / "short-jobs"

    def _job_file(self, job_id: str) -> Path:
        return self._history_dir / f"{job_id}.json"

    def _persist(self, job: ShortJob) -> None:
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
                job = ShortJob.model_validate(json.loads(path.read_text("utf-8")))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("skipping unreadable short job %s: %s", path.name, exc)
                continue

            if job.is_active and not _process_alive(job.pid):
                job.status = JobStatus.INTERRUPTED
                job.message = (
                    "Yarıda kaldı — kısa video hazırlanırken uygulama kapandı."
                )
                job.finished_at = job.finished_at or datetime.now(timezone.utc)
                job.error_code = ErrorCode.RENDER_FAILED.value
                job.error_message = "Kısa video tamamlanamadan yarıda kesildi."
                job.error_suggestion = (
                    "Tekrar deneyin. Aynı bölümler, kırpmalar ve düzen kullanılır; hâlihazırda "
                    "kesilmiş parçalar yeniden kesilmez."
                )
                self._persist(job)
                interrupted += 1

            self._jobs[job.id] = job

        if interrupted:
            logger.warning("marked %d interrupted Short(s) from a previous session", interrupted)
        self._prune_history()

    def _prune_history(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
        finished = sorted(
            (j for j in self._jobs.values() if j.is_terminal),
            key=lambda j: j.created_at,
            reverse=True,
        )
        for position, job in enumerate(finished):
            if job.created_at < cutoff or position >= MAX_HISTORY_ENTRIES:
                self._jobs.pop(job.id, None)
                self._job_file(job.id).unlink(missing_ok=True)

    # --- lifecycle --------------------------------------------------------

    def ensure_history(self) -> None:
        """Read the on-disk history exactly once per process."""
        if self._history_loaded:
            return
        self._history_loaded = True
        self.load_history()

    async def start(self) -> None:
        self._bind_loop()
        self.ensure_history()
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
        self._loop = None

    # --- public API -------------------------------------------------------

    def list_jobs(self, *, project_slug: str | None = None, limit: int = 50) -> list[ShortJob]:
        self.ensure_history()
        jobs = [
            job for job in self._jobs.values()
            if project_slug is None or job.project_slug == project_slug
        ]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get(self, job_id: str) -> ShortJob:
        self.ensure_history()
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                ErrorCode.SHORT_JOB_NOT_FOUND,
                f"'{job_id}' numaralı bir kısa video işlemi bulunamadı.",
            )
        return job

    def active_job(self, *, project_slug: str | None = None) -> ShortJob | None:
        self.ensure_history()
        return next(
            (
                j for j in self._jobs.values()
                if j.is_active and (project_slug is None or j.project_slug == project_slug)
            ),
            None,
        )

    def active_for_cache_key(self, slug: str, cache_key: str) -> ShortJob | None:
        return next(
            (
                j for j in self._jobs.values()
                if j.is_active and j.project_slug == slug and j.cache_key == cache_key
            ),
            None,
        )

    async def submit(self, project_slug: str, request: ShortRequest) -> ShortJob:
        """Queue a Short, or hand back the identical one that already exists.

        Validation happens *before* a job exists, so an impossible request fails
        as a 4xx the caller can act on rather than as a job that fails a second
        later with the same message.
        """
        self._bind_loop()
        self.ensure_history()
        assert self._lock is not None and self._queue is not None

        manifest, source = self.service.load_source(project_slug, request.source_render_id)
        # Captions are resolved before a job exists, so "this render has no clean
        # master" is a 4xx the page can explain rather than a job that fails a
        # second later. Nothing falls back to the captioned export here.
        if request.caption_mode.needs_clean_master:
            self._resolve_clean_source(project_slug, manifest)
        plan = build_plan(manifest, request)

        async with self._lock:
            existing = self.active_for_cache_key(project_slug, plan.cache_key)
            if existing is not None:
                logger.info(
                    "reusing in-flight Short job %s for cache key %s",
                    existing.id, plan.cache_key,
                )
                return existing

            record = self.service.find_by_cache_key(project_slug, plan.cache_key)
            if record is not None:
                return self._completed_from_cache(project_slug, request, plan, record)

            job = ShortJob(
                project_slug=project_slug,
                request=request,
                cache_key=plan.cache_key,
                short_id=plan.cache_key,
                source_render_id=request.source_render_id,
                source_video=source.filename,
                section_numbers=[s.number for s in plan.segments],
                total_duration_seconds=plan.total_duration_seconds,
                segment_count=len(plan.segments),
                group_count=len(plan.groups),
                warnings=list(plan.warnings),
            )
            job.pid = os.getpid()
            self._jobs[job.id] = job
            self._persist(job)
            await self._queue.put(job.id)

        logger.info("queued Short job %s for %s (%d cut(s))",
                    job.id, project_slug, len(plan.groups))
        await self.start()
        return job

    def _resolve_clean_source(
        self, slug: str, manifest: ShortRenderManifest
    ) -> tuple[Path, CueSidecar]:
        """Fully verify the clean master and its cue data, or refuse the job.

        Checksums, ffprobe, schema versions and the render/snapshot binding are
        all checked here. Every failure raises; there is deliberately no path
        that quietly continues with the captioned export, because a Short built
        that way would carry the source's small captions *and* the large ones.
        """
        paths = self.service.paths_for(slug)
        master = self.service.clean_master_path(paths, manifest)
        if master is None:
            raise ValidationError(
                ErrorCode.SHORT_CAPTIONS_UNAVAILABLE,
                "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak "
                "için uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden "
                "oluşturun.",
                details="bu videonun kayıtlarında altyazısız kopya bilgisi yok",
                suggestion=(
                    "Ya da bu kısa videoyu “Videodaki altyazıyı kullan” seçeneğiyle oluşturun."
                ),
            )
        cues = self.service.load_caption_cues(paths, manifest)
        return master, cues

    def _completed_from_cache(
        self, slug: str, request: ShortRequest, plan, record  # noqa: ANN001
    ) -> ShortJob:
        """Record a completed job for a Short that already existed on disk."""
        job = ShortJob(
            project_slug=slug,
            request=request,
            cache_key=plan.cache_key,
            short_id=record.short_id,
            source_render_id=request.source_render_id,
            source_video=record.source_video,
            section_numbers=list(record.section_numbers),
            status=JobStatus.COMPLETED,
            phase=ShortPhase.PUBLISH,
            progress=1.0,
            message="Bu projede birebir aynısı olduğu için mevcut dosya kullanıldı.",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            output_file=record.filename,
            artifacts=list(record.artifacts),
            cache_reused=True,
            total_duration_seconds=record.duration_seconds,
            segment_count=len(plan.segments),
            group_count=len(plan.groups),
        )
        self._jobs[job.id] = job
        self._persist(job)
        logger.info("cache hit: Short %s reused for %s", record.short_id, slug)
        return job

    async def cancel(self, job_id: str) -> ShortJob:
        job = self.get(job_id)
        if job.is_terminal:
            raise ConflictError(
                ErrorCode.SHORT_JOB_NOT_FOUND,
                "Bu kısa video işlemi zaten tamamlanmış.",
                suggestion="Bunun yerine yeni bir kısa video oluşturun.",
            )
        running = self._running.get(job_id)
        if running is not None:
            running.cancel_event.set()
            logger.info("cancellation requested for running Short %s", job_id)
        else:
            self._finalize(job, JobStatus.CANCELLED, "Başlamadan iptal edildi.")
        return job

    async def retry(self, job_id: str) -> ShortJob:
        """Queue the same request again, verbatim."""
        job = self.get(job_id)
        if job.is_active:
            raise ConflictError(
                ErrorCode.SHORT_JOB_NOT_FOUND,
                "Bu kısa video hâlâ hazırlanıyor.",
                suggestion="Önce iptal edin ya da bitmesini bekleyin.",
            )
        return await self.submit(job.project_slug, job.request)

    async def subscribe(self, job_id: str) -> AsyncIterator[ShortJobEvent]:
        job = self.get(job_id)

        if job.is_terminal:
            yield _event_for(job)
            return

        queue: asyncio.Queue[ShortJobEvent] = asyncio.Queue()
        running = self._running.get(job_id)
        if running is None:
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
                    event = _event_for(self.get(job_id))  # heartbeat
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
                async with render_slot(label=f"short job {job_id}"):
                    if job.is_terminal:  # cancelled while it waited for the slot
                        continue
                    await self._run_job(job)
            except Exception:  # noqa: BLE001 - the worker must survive any job
                logger.exception("Short job %s crashed the worker loop", job_id)

    async def _run_job(self, job: ShortJob) -> None:
        cancel_event = asyncio.Event()
        running = _RunningShort(job=job, cancel_event=cancel_event)
        self._running[job.id] = running

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        job.pid = os.getpid()
        job.message = "Başlıyor"
        self._persist(job)
        self._broadcast(running)

        try:
            manifest, _ = self.service.load_source(job.project_slug, job.request.source_render_id)
            clean_master: Path | None = None
            cue_sidecar: CueSidecar | None = None
            if job.request.caption_mode.needs_clean_master:
                clean_master, cue_sidecar = self._resolve_clean_source(
                    job.project_slug, manifest
                )
            plan = build_plan(manifest, job.request)
            paths = self.service.paths_for(job.project_slug)

            def on_progress(phase: ShortPhase, overall: float, message: str) -> None:
                job.phase = phase
                job.progress = overall
                job.message = message
                self._broadcast(running)

            pipeline = ShortsPipeline(
                paths=paths,
                manifest=manifest,
                request=job.request,
                plan=plan,
                settings=self.settings,
                on_progress=on_progress,
                cancel_event=cancel_event,
                job_id=job.id,
                clean_master=clean_master,
                cue_sidecar=cue_sidecar,
            )
            result = await pipeline.run()

            job.short_id = result.short_manifest.short_id
            job.cache_key = result.plan.cache_key
            job.output_file = result.artifacts.video.name
            job.artifacts = artifacts_for(job.project_slug, result.artifacts)
            job.log_file = result.artifacts.log.name if result.artifacts.log else None
            job.warnings = list(result.warnings)
            job.total_duration_seconds = result.plan.total_duration_seconds
            job.segment_count = len(result.plan.segments)
            job.group_count = len(result.plan.groups)
            job.section_numbers = [s.number for s in result.plan.segments]
            self._finalize(job, JobStatus.COMPLETED, "Kısa video hazır.", running)

        except CancelledRender:
            logger.info("Short job %s cancelled", job.id)
            self._finalize(job, JobStatus.CANCELLED, "İptal edildi.", running)

        except AppError as exc:
            logger.warning("Short job %s failed: %s", job.id, exc)
            job.error_code = exc.code.value
            job.error_message = exc.message
            job.error_details = exc.details
            job.error_suggestion = exc.suggestion
            self._finalize(job, JobStatus.FAILED, exc.message, running)

        except Exception as exc:  # noqa: BLE001
            import traceback

            logger.exception("Short job %s failed unexpectedly", job.id)
            job.error_code = ErrorCode.INTERNAL.value
            job.error_message = "Beklenmedik bir hata kısa video oluşturmayı durdurdu."
            job.error_details = traceback.format_exc()[-4000:]
            job.error_suggestion = (
                "Bu bir yazılım hatası. Ayrıntılar yukarıda ve kayıt dosyasında yer alıyor."
            )
            self._finalize(job, JobStatus.FAILED, str(exc), running)

        finally:
            self._running.pop(job.id, None)

    def _finalize(
        self,
        job: ShortJob,
        status: JobStatus,
        message: str,
        running: _RunningShort | None = None,
    ) -> None:
        job.status = status
        job.message = message
        job.finished_at = datetime.now(timezone.utc)
        job.progress = 1.0 if status is JobStatus.COMPLETED else job.progress
        job.pid = None
        self._persist(job)
        if running is not None:
            self._broadcast(running)

    def _broadcast(self, running: _RunningShort) -> None:
        event = _event_for(running.job)
        for queue in list(running.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        if running.job.is_terminal or int(running.job.progress * 100) % 5 == 0:
            self._persist(running.job)


def _event_for(job: ShortJob) -> ShortJobEvent:
    return ShortJobEvent(
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


#: One manager per process.
_manager: ShortJobManager | None = None


def get_short_job_manager() -> ShortJobManager:
    global _manager
    if _manager is None:
        _manager = ShortJobManager(get_settings())
    return _manager


def reset_short_job_manager() -> None:
    """Drop the singleton. Used by tests to isolate state."""
    global _manager
    _manager = None
