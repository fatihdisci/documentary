"""The publishing job queue.

Same contract as the render and Shorts queues — one job at a time, every state
change written to disk, progress over SSE with a polling fallback, and a job
killed mid-run reported as *interrupted* rather than sitting in "running" — but
its own queue and its own history, because an upload is not a render.

Three things are specific to uploading, and all three shape the design:

* **It is network work, not CPU work.** Nothing here takes the process-wide
  FFmpeg render slot, so a video can upload while the next one renders. Every
  platform client is blocking, so the whole job body runs in a worker thread
  through ``anyio.to_thread.run_sync`` and never touches the event loop.

* **Half of it is not repeatable.** Once a platform has the video, sending it
  again creates a second post. So the id is written to the job record and to the
  project's history *the instant it exists*, and every later step is allowed to
  fail on its own. A retry of such a job resumes from the failed step and can
  never re-upload the file.

* **Every platform is independent.** One job per platform, one history entry per
  platform, one duplicate check per platform. Instagram failing says nothing
  about Facebook and must never cause a re-upload anywhere else — which is why
  the duplicate checks below are all scoped by ``platform`` and never by file
  alone.

The platforms differ in the middle of the run and nowhere else. Validate,
authenticate, fingerprint and the duplicate guard are shared; then
``_execute_youtube``, ``_execute_meta`` and ``_execute_tiktok`` take over.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio.to_thread

from app.config import Settings, get_settings
from app.errors import AppError, ConflictError, ErrorCode, NotFoundError
from app.models.enums import JobStatus
from app.publishing.hosting import MediaHost, resolve_media_host
from app.publishing.meta import MetaClient, MetaCredentials
from app.publishing.models import (
    AssetStatus,
    MediaItem,
    PublishDraft,
    PublishHistoryEntry,
    PublishJob,
    PublishJobEvent,
    PublishMode,
    PublishPhase,
    PublishRequest,
    PublishingPlatform,
    SocialDraft,
    SourceFingerprint,
)
from app.publishing.repository import PublishingRepository
from app.publishing.service import (
    PublishingService,
    compose_caption,
    resolve_publish_at,
    sha256_file,
)
from app.publishing.tiktok import TikTokClient, TikTokCredentials
from app.publishing.youtube import (
    UploadCancelled,
    YouTubeClient,
    YouTubeCredentials,
    map_api_error,
)
from app.render.jobs import _process_alive

logger = logging.getLogger("evb.publishing.jobs")

HISTORY_RETENTION_DAYS = 60
MAX_HISTORY_ENTRIES = 200

#: Where each phase sits on the overall progress bar, per platform. The part
#: whose duration depends on the file owns most of the bar: for YouTube and
#: TikTok that is sending the bytes, for Meta it is parking them somewhere Meta
#: can fetch from and then waiting on Meta's own transcoder.
_YOUTUBE_PHASES = {
    PublishPhase.VALIDATE: 0.0,
    PublishPhase.AUTHENTICATE: 0.03,
    PublishPhase.HASH_SOURCE: 0.06,
    PublishPhase.UPLOAD_VIDEO: 0.12,
    PublishPhase.SET_THUMBNAIL: 0.84,
    PublishPhase.UPLOAD_CAPTIONS: 0.90,
    PublishPhase.FETCH_STATUS: 0.96,
    PublishPhase.COMPLETE: 1.0,
}
_META_PHASES = {
    PublishPhase.VALIDATE: 0.0,
    PublishPhase.AUTHENTICATE: 0.03,
    PublishPhase.HASH_SOURCE: 0.06,
    PublishPhase.HOST_MEDIA: 0.10,
    PublishPhase.CREATE_CONTAINER: 0.55,
    PublishPhase.AWAIT_PROCESSING: 0.62,
    PublishPhase.PUBLISH_POST: 0.90,
    PublishPhase.CLEANUP: 0.95,
    PublishPhase.FETCH_STATUS: 0.97,
    PublishPhase.COMPLETE: 1.0,
}
_TIKTOK_PHASES = {
    PublishPhase.VALIDATE: 0.0,
    PublishPhase.AUTHENTICATE: 0.03,
    PublishPhase.HASH_SOURCE: 0.06,
    PublishPhase.CREATE_CONTAINER: 0.10,
    PublishPhase.UPLOAD_VIDEO: 0.15,
    PublishPhase.AWAIT_PROCESSING: 0.85,
    PublishPhase.FETCH_STATUS: 0.96,
    PublishPhase.COMPLETE: 1.0,
}
_PLATFORM_PHASES = {
    PublishingPlatform.YOUTUBE: _YOUTUBE_PHASES,
    PublishingPlatform.INSTAGRAM: _META_PHASES,
    PublishingPlatform.FACEBOOK: _META_PHASES,
    PublishingPlatform.TIKTOK: _TIKTOK_PHASES,
}

#: Kept as a module constant because the YouTube progress callback is written in
#: terms of it and reads better that way.
_PHASE_START = _YOUTUBE_PHASES
_UPLOAD_SPAN = _PHASE_START[PublishPhase.SET_THUMBNAIL] - _PHASE_START[PublishPhase.UPLOAD_VIDEO]
_TIKTOK_UPLOAD_SPAN = (
    _TIKTOK_PHASES[PublishPhase.AWAIT_PROCESSING] - _TIKTOK_PHASES[PublishPhase.UPLOAD_VIDEO]
)


def _phase_start(platform: PublishingPlatform, phase: PublishPhase) -> float:
    """Where a phase sits on one platform's bar; 0 for a phase it never runs."""
    return _PLATFORM_PHASES.get(platform, _YOUTUBE_PHASES).get(phase, 0.0)


@dataclass
class _RunningPublish:
    job: PublishJob
    cancel_event: threading.Event
    subscribers: list[asyncio.Queue[PublishJobEvent]] = field(default_factory=list)


@dataclass
class _JobPlan:
    """Immutable inputs for one run, resolved before the thread starts."""

    media: MediaItem
    draft: PublishDraft
    video: Path
    thumbnail: Path | None
    caption: Path | None
    publish_at: datetime | None
    #: The platform's own text block. ``None`` for YouTube, which has its own.
    social: SocialDraft | None = None

    @property
    def title(self) -> str:
        """What this post is called, for the history row and the job record."""
        if self.social is None:
            return self.draft.youtube.title
        first_line = self.social.caption.strip().splitlines()
        return (first_line[0] if first_line else self.media.filename)[:200]


class PublishJobManager:
    """Owns the publishing queue, worker loop and on-disk job history."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.service = PublishingService(self.settings)
        self._jobs: dict[str, PublishJob] = {}
        self._running: dict[str, _RunningPublish] = {}
        self._worker: asyncio.Task | None = None
        self._history_loaded = False
        #: Guards job-record writes, which come from the loop and a worker thread.
        self._persist_lock = threading.Lock()
        # Bound lazily to whichever loop is running, exactly as the other two
        # queues do it: a reload or the next test replaces the loop.
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
        return self.settings.data_dir / "publishing-jobs"

    def _job_file(self, job_id: str) -> Path:
        return self._history_dir / f"{job_id}.json"

    def _persist(self, job: PublishJob) -> None:
        """Write the job record atomically, from any thread.

        Unlike the render and Shorts queues, this one is persisted from two
        places at once: the worker thread running the upload, and the event loop
        broadcasting progress. So the temp name is unique per write and the whole
        serialize-and-rename is under a lock — otherwise two writers share one
        temp path and the second finds it already renamed away.
        """
        self._history_dir.mkdir(parents=True, exist_ok=True)
        target = self._job_file(job.id)
        with self._persist_lock:
            payload = job.model_dump_json(indent=2)
            tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(payload, "utf-8")
            tmp.replace(target)

    def load_history(self) -> None:
        """Read every stored job, marking any that were killed mid-upload."""
        self._history_dir.mkdir(parents=True, exist_ok=True)
        interrupted = 0

        for path in sorted(self._history_dir.glob("*.json")):
            try:
                job = PublishJob.model_validate(json.loads(path.read_text("utf-8")))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("skipping unreadable publish job %s: %s", path.name, exc)
                continue

            if job.is_active and not _process_alive(job.pid):
                job.status = JobStatus.INTERRUPTED
                job.message = "Yarıda kaldı — yükleme sırasında uygulama kapandı."
                job.finished_at = job.finished_at or datetime.now(timezone.utc)
                job.error_code = ErrorCode.YOUTUBE_UPLOAD_FAILED.value
                job.error_message = "Yükleme tamamlanamadan yarıda kesildi."
                job.error_suggestion = (
                    "“Tekrar dene” düğmesine basın. Video YouTube'a ulaştıysa ikinci kez "
                    "yüklenmez; yalnızca kalan adımlar tekrarlanır."
                    if job.video_id
                    else "“Tekrar dene” düğmesine basın; yükleme baştan başlar."
                )
                self._persist(job)
                interrupted += 1

            self._jobs[job.id] = job

        if interrupted:
            logger.warning("marked %d interrupted upload(s) from a previous session", interrupted)
        self._prune_history()

    def _prune_history(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
        finished = sorted(
            (job for job in self._jobs.values() if job.is_terminal),
            key=lambda job: job.created_at,
            reverse=True,
        )
        for position, job in enumerate(finished):
            if job.created_at < cutoff or position >= MAX_HISTORY_ENTRIES:
                self._jobs.pop(job.id, None)
                self._job_file(job.id).unlink(missing_ok=True)

    # --- lifecycle --------------------------------------------------------

    def ensure_history(self) -> None:
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

    def list_jobs(self, *, project_slug: str | None = None, limit: int = 50) -> list[PublishJob]:
        self.ensure_history()
        jobs = [
            job
            for job in self._jobs.values()
            if project_slug is None or job.project_slug == project_slug
        ]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def get(self, job_id: str) -> PublishJob:
        self.ensure_history()
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                ErrorCode.PUBLISHING_JOB_NOT_FOUND,
                f"'{job_id}' numaralı bir yükleme işlemi bulunamadı.",
            )
        return job

    def active_job(self, *, project_slug: str | None = None) -> PublishJob | None:
        self.ensure_history()
        return next(
            (
                job
                for job in self._jobs.values()
                if job.is_active
                and (project_slug is None or job.project_slug == project_slug)
            ),
            None,
        )

    async def submit_youtube(self, slug: str, request: PublishRequest) -> PublishJob:
        """Queue one YouTube upload. Everything fixable is checked first."""
        return await self.submit(slug, PublishingPlatform.YOUTUBE, request)

    async def submit(
        self, slug: str, platform: PublishingPlatform, request: PublishRequest
    ) -> PublishJob:
        """Queue one upload to one platform. Everything fixable is checked first.

        The whole method is scoped to ``platform``: the same file may be queued
        for Instagram while it is uploading to YouTube, and neither guard below
        can see the other's job.
        """
        self._bind_loop()
        self.ensure_history()
        assert self._lock is not None and self._queue is not None

        media, draft, _video, warnings = self.service.prepare_upload(
            slug,
            request.media_id,
            allow_duplicate=request.allow_duplicate,
            platform=platform,
            allowed_privacy=self._allowed_privacy(platform),
        )

        async with self._lock:
            existing = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.is_active
                    and job.project_slug == slug
                    and job.platform is platform
                    and job.media_id == request.media_id
                ),
                None,
            )
            if existing is not None:
                raise ConflictError(
                    ErrorCode.PUBLISHING_DUPLICATE,
                    f"Bu dosya için {platform.label} yüklemesi zaten sürüyor.",
                    details=f"işlem numarası {existing.id}",
                    suggestion="Bitmesini bekleyin ya da onu iptal edip yeniden başlatın.",
                )

            # ``prepare_upload`` only sees uploads that *finished* — they are the
            # ones in the history. The same bytes reaching the queue under a
            # second media id (a re-render kept beside the first, a copy) would
            # sail past it while the first upload is still queued or running, and
            # arrive on the platform as a second post. So the queue itself is
            # checked here, by fingerprint, under the same lock that adds jobs.
            duplicate = self._active_with_fingerprint(
                slug,
                media.fingerprint.sha256,
                platform=platform,
                allow_duplicate=request.allow_duplicate,
            )
            if duplicate is not None:
                raise ConflictError(
                    ErrorCode.PUBLISHING_DUPLICATE,
                    f"Aynı video dosyası için bir {platform.label} yüklemesi zaten sırada.",
                    details=(
                        f"'{duplicate.media_id}' aynı dosyanın kendisi "
                        f"(işlem numarası {duplicate.id})"
                    ),
                    suggestion=(
                        "Süren yüklemenin bitmesini bekleyin. Gerçekten ikinci bir gönderi "
                        "oluşturmak istiyorsanız “Yine de yeni olarak yükle” seçeneğini "
                        "işaretleyin."
                    ),
                )

            job = PublishJob(
                project_slug=slug,
                media_id=request.media_id,
                platform=platform,
                source=media.fingerprint,
                allow_duplicate=request.allow_duplicate,
                title=_title_for(draft, platform, media),
                requested_privacy_status=draft.youtube.privacy_status,
                total_bytes=media.size_bytes,
                warnings=list(warnings),
                pid=os.getpid(),
            )
            # Only YouTube can be scheduled. Meta's Reels APIs and TikTok's
            # Direct Post both publish immediately, so recording a requested
            # time for them would put a promise on the job that nothing keeps.
            if (
                platform is PublishingPlatform.YOUTUBE
                and draft.youtube.publish_mode is PublishMode.SCHEDULE
            ):
                job.requested_publish_at = resolve_publish_at(
                    draft.youtube.publish_at_local
                )
            self._jobs[job.id] = job
            self._persist(job)
            await self._queue.put(job.id)

        logger.info(
            "queued %s upload %s for %s (%s)", platform.value, job.id, slug, request.media_id
        )
        await self.start()
        return job

    def _allowed_privacy(self, platform: PublishingPlatform) -> list[str] | None:
        """TikTok's current privacy options, so a refusal happens before a job.

        Read from the cached connection rather than the network: this runs on
        the event loop, and a stale-but-cached answer still catches the common
        mistake (public on an unaudited app) instantly.
        """
        if platform is not PublishingPlatform.TIKTOK:
            return None
        try:
            connection = TikTokCredentials(self.settings).status()
        except AppError:
            return None
        info = connection.creator_info
        return list(info.privacy_level_options) if info else None

    def _active_with_fingerprint(
        self,
        slug: str,
        sha256: str,
        *,
        platform: PublishingPlatform,
        allow_duplicate: bool,
    ) -> PublishJob | None:
        """A queued or running upload of the same *bytes* to the same platform.

        Scoped to one project and one platform, exactly like the history-based
        check, and skipped entirely when the user has confirmed they want a
        second post.
        """
        if allow_duplicate or not sha256:
            return None
        return next(
            (
                job
                for job in self._jobs.values()
                if job.is_active
                and job.project_slug == slug
                and job.platform is platform
                and job.source.sha256 == sha256
            ),
            None,
        )

    async def cancel(self, job_id: str) -> PublishJob:
        job = self.get(job_id)
        if job.is_terminal:
            raise ConflictError(
                ErrorCode.PUBLISHING_JOB_NOT_FOUND,
                "Bu yükleme zaten tamamlanmış.",
                suggestion="Bunun yerine yeni bir yükleme başlatın.",
            )
        running = self._running.get(job_id)
        if running is not None:
            running.cancel_event.set()
            logger.info("cancellation requested for upload %s", job_id)
        else:
            self._finalize(job, JobStatus.CANCELLED, "Başlamadan iptal edildi.")
        return job

    async def retry(self, job_id: str) -> PublishJob:
        """Run the same upload again — resuming, never repeating, a finished one.

        A job that already has a video id only re-runs the steps after the
        upload. That is what makes "Tekrar dene" safe after a thumbnail or
        caption failure.
        """
        self._bind_loop()
        previous = self.get(job_id)
        if previous.is_active:
            raise ConflictError(
                ErrorCode.PUBLISHING_JOB_NOT_FOUND,
                "Bu yükleme hâlâ sürüyor.",
                suggestion="Önce iptal edin ya da bitmesini bekleyin.",
            )
        assert self._queue is not None and self._lock is not None

        if previous.video_id:
            job = PublishJob(
                project_slug=previous.project_slug,
                media_id=previous.media_id,
                platform=previous.platform,
                source=previous.source,
                # A resume: the post already exists, so the duplicate guards
                # must not stop the steps that still have to run.
                allow_duplicate=True,
                title=previous.title,
                requested_privacy_status=previous.requested_privacy_status,
                requested_publish_at=previous.requested_publish_at,
                total_bytes=previous.total_bytes,
                uploaded_bytes=previous.total_bytes,
                video_id=previous.video_id,
                video_url=previous.video_url,
                container_id=previous.container_id,
                # Carried so the temporary copy of a Meta upload is still
                # cleaned up even though this run will not create a new one.
                hosted_object_key=previous.hosted_object_key,
                thumbnail_status=previous.thumbnail_status,
                caption_status=previous.caption_status,
                caption_track_id=previous.caption_track_id,
                pid=os.getpid(),
            )
            async with self._lock:
                self._jobs[job.id] = job
                self._persist(job)
                await self._queue.put(job.id)
            logger.info("queued resume of upload %s as %s", job_id, job.id)
            await self.start()
            return job

        return await self.submit(
            previous.project_slug,
            previous.platform,
            PublishRequest(media_id=previous.media_id, allow_duplicate=True),
        )

    async def subscribe(self, job_id: str) -> AsyncIterator[PublishJobEvent]:
        job = self.get(job_id)

        if job.is_terminal:
            yield _event_for(job)
            return

        queue: asyncio.Queue[PublishJobEvent] = asyncio.Queue()
        running = self._running.get(job_id)
        if running is None:
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
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.INTERRUPTED,
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
                await self._run_job(job)
            except Exception:  # noqa: BLE001 - the worker must survive any job
                logger.exception("upload %s crashed the worker loop", job_id)

    async def _run_job(self, job: PublishJob) -> None:
        cancel_event = threading.Event()
        running = _RunningPublish(job=job, cancel_event=cancel_event)
        self._running[job.id] = running
        loop = asyncio.get_running_loop()

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        job.pid = os.getpid()
        job.message = "Başlıyor"
        self._persist(job)
        self._broadcast(running)

        def report() -> None:
            """Push the job's current state to subscribers, from any thread."""
            loop.call_soon_threadsafe(self._broadcast, running)

        try:
            plan = await anyio.to_thread.run_sync(self._resolve_plan, job)
            await anyio.to_thread.run_sync(self._execute, job, plan, cancel_event, report)
            self._finalize(job, JobStatus.COMPLETED, _completion_message(job), running)

        except UploadCancelled:
            logger.info("upload %s cancelled", job.id)
            self._finalize(job, JobStatus.CANCELLED, "İptal edildi.", running)

        except AppError as exc:
            logger.warning("upload %s failed: %s", job.id, exc)
            job.error_code = exc.code.value
            job.error_message = exc.message
            job.error_details = exc.details
            job.error_suggestion = exc.suggestion
            self._finalize(job, JobStatus.FAILED, exc.message, running)

        except Exception as exc:  # noqa: BLE001
            import traceback

            logger.exception("upload %s failed unexpectedly", job.id)
            job.error_code = ErrorCode.INTERNAL.value
            job.error_message = "Beklenmedik bir hata yüklemeyi durdurdu."
            job.error_details = traceback.format_exc()[-4000:]
            job.error_suggestion = (
                "Bu bir yazılım hatası. Ayrıntılar yukarıda ve kayıt dosyasında yer alıyor."
            )
            self._finalize(job, JobStatus.FAILED, str(exc), running)

        finally:
            self._running.pop(job.id, None)

    # --- the actual work (all of it blocking, all of it off the loop) -----

    def _resolve_plan(self, job: PublishJob) -> _JobPlan:
        """Re-resolve everything from disk at run time, not at submit time."""
        media = self.service.get_media(job.project_slug, job.media_id)
        repository = PublishingRepository(self.service.paths_for(job.project_slug))
        draft = repository.get_draft(job.media_id)
        if draft is None:
            raise NotFoundError(
                ErrorCode.PUBLISHING_MEDIA_NOT_FOUND,
                "Bu dosyanın yayın bilgileri bulunamadı.",
                details=f"media id: {job.media_id}",
            )

        social = (
            None
            if job.platform is PublishingPlatform.YOUTUBE
            else draft.social(job.platform)
        )

        thumbnail = None
        caption = None
        publish_at = None
        if social is None:
            thumbnail = (
                repository.thumbnail_path(draft.youtube.thumbnail_file)
                if draft.youtube.thumbnail_file
                else None
            )
            if draft.youtube.upload_captions and draft.youtube.caption_file:
                caption = self.service.caption_path(
                    job.project_slug, draft.youtube.caption_file, draft.youtube.caption_source
                )
            if draft.youtube.publish_mode is PublishMode.SCHEDULE:
                publish_at = resolve_publish_at(draft.youtube.publish_at_local)

        return _JobPlan(
            media=media,
            draft=draft,
            video=self.service.media_path(job.project_slug, job.media_id),
            thumbnail=thumbnail,
            caption=caption,
            publish_at=publish_at,
            social=social,
        )

    def _execute(
        self,
        job: PublishJob,
        plan: _JobPlan,
        cancel_event: threading.Event,
        report: Callable[[], None],
    ) -> None:
        repository = PublishingRepository(self.service.paths_for(job.project_slug))

        def advance(phase: PublishPhase, message: str) -> None:
            job.phase = phase
            job.progress = _phase_start(job.platform, phase)
            job.message = message
            report()

        def check_cancel() -> None:
            if cancel_event.is_set():
                raise UploadCancelled()

        advance(PublishPhase.VALIDATE, "Dosya ve bilgiler kontrol ediliyor")
        check_cancel()

        # Shared prologue: fingerprint the file and re-check the duplicate guard
        # against the history, which may have gained an entry while this job sat
        # in the queue. A job that already owns a post id is resuming and skips
        # all of it — it must never send the bytes a second time.
        if not job.video_id:
            advance(PublishPhase.HASH_SOURCE, "Dosya parmak izi hesaplanıyor")
            digest = sha256_file(plan.video)
            recorded = plan.draft.source_fingerprint.sha256 or plan.media.fingerprint.sha256
            if recorded and digest != recorded:
                raise AppError(
                    ErrorCode.PUBLISHING_SOURCE_CHANGED,
                    "Yükleme başlamadan önce video dosyasının değiştiği görüldü.",
                    details=f"beklenen sha256 {recorded[:16]}…, diskteki {digest[:16]}…",
                    http_status=409,
                )
            job.source = SourceFingerprint(
                filename=plan.video.name,
                size_bytes=plan.video.stat().st_size,
                sha256=digest,
            )
            job.total_bytes = job.source.size_bytes
            self._refuse_known_duplicate(repository, job, digest)
            check_cancel()

        if job.platform is PublishingPlatform.YOUTUBE:
            self._execute_youtube(repository, job, plan, cancel_event, advance, report)
        elif job.platform is PublishingPlatform.TIKTOK:
            self._execute_tiktok(repository, job, plan, cancel_event, advance, report)
        else:
            self._execute_meta(repository, job, plan, cancel_event, advance, report)

        job.phase = PublishPhase.COMPLETE
        job.progress = 1.0
        self._record_history(repository, job, plan)
        report()

    def _refuse_known_duplicate(
        self, repository: PublishingRepository, job: PublishJob, digest: str
    ) -> None:
        """Last check before the bytes leave: is this file already on *this* platform?

        Queueing is not publishing. Between the submit and this line another job
        may have put exactly these bytes on the same platform, and the history —
        which is written the instant a post id exists — is the record of that.

        Two things it deliberately does not do: stop a job that already owns a
        post id (that job is resuming, not repeating), and look at other
        platforms (a Reel on Instagram is not a reason to refuse a YouTube
        upload, and vice versa).
        """
        if job.allow_duplicate or job.video_id:
            return
        duplicate = repository.find_upload(sha256=digest, platform=job.platform.value)
        if duplicate is None or duplicate.job_id == job.id:
            return
        raise ConflictError(
            ErrorCode.PUBLISHING_DUPLICATE,
            f"Bu dosya bu yükleme sıradayken {job.platform.label}'a yüklenmiş.",
            details=(
                f"{duplicate.title}\n{duplicate.video_url}\n"
                f"yüklenme: {duplicate.uploaded_at.isoformat()}"
            ),
            suggestion=(
                "İkinci bir gönderi oluşturmak istiyorsanız “Yine de yeni olarak "
                "yükle” seçeneğini işaretleyip tekrar deneyin."
            ),
            video_id=duplicate.video_id,
            video_url=duplicate.video_url,
        )

    # --- YouTube ----------------------------------------------------------

    def _execute_youtube(
        self,
        repository: PublishingRepository,
        job: PublishJob,
        plan: _JobPlan,
        cancel_event: threading.Event,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        advance(PublishPhase.AUTHENTICATE, "YouTube bağlantısı doğrulanıyor")
        credentials = YouTubeCredentials(self.settings).usable_credentials()
        client = YouTubeClient(credentials)
        if cancel_event.is_set():
            raise UploadCancelled()

        if not job.video_id:
            self._upload_video(job, plan, client, cancel_event, report)
            # The video now exists on the channel. Record it before anything
            # else runs, so a crash one line later cannot cause a second upload.
            self._record_history(repository, job, plan)

        self._set_thumbnail(job, plan, client, advance, report)
        self._upload_captions(job, plan, client, advance, report)
        self._fetch_status(job, client, advance)

    # --- Instagram and Facebook -------------------------------------------

    def _execute_meta(
        self,
        repository: PublishingRepository,
        job: PublishJob,
        plan: _JobPlan,
        cancel_event: threading.Event,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        """Host the file, hand Meta the link, wait, then publish.

        Meta never receives a path or a file: it is given a temporary URL and
        downloads the video itself. The object is removed again afterwards, and
        it is removed whether the publish succeeded or not — a failed job must
        not leave a video sitting on the internet.
        """
        instagram = job.platform is PublishingPlatform.INSTAGRAM
        assert plan.social is not None
        caption = compose_caption(plan.social)

        advance(PublishPhase.AUTHENTICATE, "Meta bağlantısı doğrulanıyor")
        target = MetaCredentials(self.settings).target()
        if instagram:
            target.require_instagram()
        client = self._meta_client(target)
        job.warnings.extend(
            _meta_destination_note(job.platform, target, plan.social)
        )
        if cancel_event.is_set():
            raise UploadCancelled()

        host: MediaHost | None = None
        try:
            if not job.video_id:
                advance(PublishPhase.HOST_MEDIA, "Video geçici adrese yükleniyor")
                host = resolve_media_host(self.settings)
                hosted = host.put(plan.video, key_hint=plan.media.filename)
                job.hosted_object_key = hosted.object_key
                job.uploaded_bytes = job.total_bytes
                self._persist(job)
                report()
                if cancel_event.is_set():
                    raise UploadCancelled()

                if instagram:
                    self._publish_instagram(
                        job,
                        client,
                        hosted.url,
                        caption,
                        share_to_feed=plan.draft.instagram.share_to_feed,
                        advance=advance,
                        report=report,
                    )
                else:
                    self._publish_facebook(job, client, hosted.url, caption, advance, report)
                # The post exists now. Record it before anything else runs.
                self._record_history(repository, job, plan)
        finally:
            if host is not None and job.hosted_object_key:
                self._cleanup_hosted(host, job, advance, report)

        advance(PublishPhase.FETCH_STATUS, "Gönderi bağlantısı okunuyor")
        if not job.video_url and job.video_id:
            permalink = (
                client.media_permalink(job.video_id)
                if instagram
                else client.page_reel_permalink(job.video_id)
            )
            if permalink:
                job.video_url = permalink
        job.upload_status = "published"
        job.processing_status = "published"

    def _meta_client(self, target: Any) -> Any:
        """Overridable seam so tests never construct a real Graph client."""
        return MetaClient(target, self.settings)

    def _publish_instagram(
        self,
        job: PublishJob,
        client: Any,
        video_url: str,
        caption: str,
        *,
        share_to_feed: bool,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        from app.publishing.meta import (
            CONTAINER_POLL_SECONDS,
            CONTAINER_TIMEOUT_SECONDS,
        )

        advance(PublishPhase.CREATE_CONTAINER, "Instagram videoyu alıyor")
        if not job.container_id:
            job.container_id = client.create_reel_container(
                video_url=video_url, caption=caption, share_to_feed=share_to_feed
            )
            self._persist(job)
        report()

        advance(PublishPhase.AWAIT_PROCESSING, "Instagram videoyu işliyor")
        deadline = time.monotonic() + CONTAINER_TIMEOUT_SECONDS
        while True:
            code, detail = client.container_status(job.container_id)
            if code == "FINISHED":
                break
            if code in {"ERROR", "EXPIRED"}:
                raise AppError(
                    ErrorCode.META_MEDIA_REJECTED,
                    "Instagram videoyu işleyemedi.",
                    details=f"durum: {code}" + (f"\n{detail}" if detail else ""),
                    http_status=422,
                )
            if time.monotonic() > deadline:
                raise AppError(
                    ErrorCode.META_MEDIA_REJECTED,
                    "Instagram videoyu işlemeyi zamanında bitirmedi.",
                    details=f"son durum: {code or 'bilinmiyor'}",
                    suggestion=(
                        "Instagram bazen geç işler. Bir süre sonra “Tekrar dene” diyebilirsiniz; "
                        "gönderi oluştuysa ikinci kez yüklenmez."
                    ),
                    http_status=504,
                )
            job.message = f"Instagram videoyu işliyor ({code.lower() or 'bekleniyor'})"
            report()
            time.sleep(CONTAINER_POLL_SECONDS)

        advance(PublishPhase.PUBLISH_POST, "Instagram'da yayınlanıyor")
        job.video_id = client.publish_container(job.container_id)
        job.video_url = client.media_permalink(job.video_id)
        self._persist(job)
        report()

    def _publish_facebook(
        self,
        job: PublishJob,
        client: Any,
        video_url: str,
        description: str,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        from app.publishing.meta import (
            CONTAINER_POLL_SECONDS,
            CONTAINER_TIMEOUT_SECONDS,
        )

        advance(PublishPhase.CREATE_CONTAINER, "Facebook videoyu alıyor")
        if not job.container_id:
            container_id, upload_url = client.start_page_reel()
            job.container_id = container_id
            self._persist(job)
            client.upload_page_reel(upload_url, video_url=video_url)
        report()

        advance(PublishPhase.AWAIT_PROCESSING, "Facebook videoyu işliyor")
        deadline = time.monotonic() + CONTAINER_TIMEOUT_SECONDS
        while True:
            status, error = client.page_reel_status(job.container_id)
            if status in {"ready", "processing_complete", "upload_complete"}:
                break
            if status in {"error", "expired"}:
                raise AppError(
                    ErrorCode.META_MEDIA_REJECTED,
                    "Facebook videoyu işleyemedi.",
                    details=f"durum: {status}" + (f"\n{error}" if error else ""),
                    http_status=422,
                )
            if time.monotonic() > deadline:
                raise AppError(
                    ErrorCode.META_MEDIA_REJECTED,
                    "Facebook videoyu işlemeyi zamanında bitirmedi.",
                    details=f"son durum: {status or 'bilinmiyor'}",
                    http_status=504,
                )
            job.message = f"Facebook videoyu işliyor ({status or 'bekleniyor'})"
            report()
            time.sleep(CONTAINER_POLL_SECONDS)

        advance(PublishPhase.PUBLISH_POST, "Facebook'ta yayınlanıyor")
        client.finish_page_reel(job.container_id, description=description)
        # Facebook's Reel keeps the id it was given at the start; publishing is
        # what turns it from a pending upload into a post, so this is the first
        # moment the id may be recorded as one.
        job.video_id = job.container_id
        job.video_url = client.page_reel_permalink(job.video_id)
        self._persist(job)
        report()

    def _cleanup_hosted(
        self,
        host: MediaHost,
        job: PublishJob,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        """Delete the temporary copy. Never fatal, and never skipped on failure."""
        if not self.settings.mutable.media_host_delete_after_publish:
            return
        advance(PublishPhase.CLEANUP, "Geçici kopya siliniyor")
        try:
            host.delete(job.hosted_object_key or "")
        except Exception:  # noqa: BLE001 - the post already exists; this is tidying
            logger.warning("temporary copy for %s could not be removed", job.id)
            job.warnings.append(
                "Geçici kopya silinemedi. Bağlantı kendiliğinden geçersiz olacak, ama "
                "kovadaki dosyayı elle de silebilirsiniz."
            )
        else:
            job.hosted_object_key = None
        self._persist(job)
        report()

    # --- TikTok -----------------------------------------------------------

    def _execute_tiktok(
        self,
        repository: PublishingRepository,
        job: PublishJob,
        plan: _JobPlan,
        cancel_event: threading.Event,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        """Init a Direct Post, send the bytes, then wait for TikTok to finish.

        No hosting layer: the file goes straight to TikTok's upload URL, so the
        video is never placed anywhere public.
        """
        from app.publishing.tiktok import (
            PUBLISH_POLL_SECONDS,
            PUBLISH_TIMEOUT_SECONDS,
            SELF_ONLY,
        )

        assert plan.social is not None
        tiktok = plan.draft.tiktok
        title = compose_caption(plan.social)

        advance(PublishPhase.AUTHENTICATE, "TikTok bağlantısı doğrulanıyor")
        credentials = TikTokCredentials(self.settings)
        client = self._tiktok_client(credentials.access_token())
        if cancel_event.is_set():
            raise UploadCancelled()

        # Asked again here, not just at submit time: an app that passed its
        # audit between the two would otherwise stay restricted, and one whose
        # audit lapsed would be caught by TikTok instead of by us.
        info = client.creator_info()
        options = [str(option).upper() for option in info.get("privacyLevelOptions") or []]
        privacy = tiktok.privacy.upper()
        if options and privacy not in options:
            raise ConflictError(
                ErrorCode.TIKTOK_PRIVACY_NOT_ALLOWED,
                f"“{tiktok.privacy}” gizliliği bu hesap için kullanılamıyor.",
                details="TikTok'un bildirdiği seçenekler: " + ", ".join(options),
            )
        if options and not set(options) - {SELF_ONLY}:
            job.warnings.append(
                "Uygulama TikTok denetiminden geçmediği için gönderi yalnızca sizin "
                "görebileceğiniz şekilde paylaşıldı."
            )

        if not job.video_id:
            advance(PublishPhase.CREATE_CONTAINER, "TikTok gönderisi hazırlanıyor")
            publish_id, upload_url, chunk_size = client.init_direct_post(
                plan.video,
                title=title,
                privacy_level=privacy,
                disable_comment=not tiktok.allow_comments,
                disable_duet=not tiktok.allow_duet,
                disable_stitch=not tiktok.allow_stitch,
            )
            job.container_id = publish_id
            self._persist(job)

            advance(PublishPhase.UPLOAD_VIDEO, "Video TikTok'a yükleniyor")

            def on_progress(sent: int, total: int) -> None:
                job.uploaded_bytes = sent
                job.total_bytes = total or job.total_bytes
                fraction = (sent / total) if total else 0.0
                job.progress = min(
                    _TIKTOK_PHASES[PublishPhase.AWAIT_PROCESSING],
                    _TIKTOK_PHASES[PublishPhase.UPLOAD_VIDEO]
                    + fraction * _TIKTOK_UPLOAD_SPAN,
                )
                job.message = (
                    f"Video yükleniyor — {sent / 1_048_576:.0f} / {total / 1_048_576:.0f} MB"
                )
                report()

            client.upload_video(
                upload_url,
                plan.video,
                chunk_size=chunk_size,
                on_progress=on_progress,
                is_cancelled=cancel_event.is_set,
            )

            advance(PublishPhase.AWAIT_PROCESSING, "TikTok gönderiyi işliyor")
            deadline = time.monotonic() + PUBLISH_TIMEOUT_SECONDS
            while True:
                status = client.publish_status(publish_id)
                state = status.get("status") or ""
                if state == "PUBLISH_COMPLETE":
                    post_ids = status.get("postIds") or []
                    # TikTok only returns a post id once the video is publicly
                    # reachable. A self-only post has none, and inventing one
                    # would be a lie — the publish id is what actually exists.
                    job.video_id = post_ids[0] if post_ids else publish_id
                    job.video_url = (
                        f"https://www.tiktok.com/@{info.get('username', '')}/video/{post_ids[0]}"
                        if post_ids
                        else ""
                    )
                    break
                if state == "FAILED":
                    raise AppError(
                        ErrorCode.TIKTOK_UPLOAD_FAILED,
                        "TikTok gönderiyi tamamlayamadı.",
                        details=f"sebep: {status.get('failReason') or 'bildirilmedi'}",
                        http_status=502,
                    )
                if time.monotonic() > deadline:
                    raise AppError(
                        ErrorCode.TIKTOK_UPLOAD_FAILED,
                        "TikTok gönderiyi zamanında tamamlamadı.",
                        details=f"son durum: {state or 'bilinmiyor'}",
                        suggestion=(
                            "TikTok uygulamasından gönderilerinizi kontrol edin. Gönderi "
                            "oluştuysa tekrar yüklemeyin."
                        ),
                        http_status=504,
                    )
                job.message = f"TikTok gönderiyi işliyor ({state.lower() or 'bekleniyor'})"
                report()
                time.sleep(PUBLISH_POLL_SECONDS)

            self._record_history(repository, job, plan)

        advance(PublishPhase.FETCH_STATUS, "Gönderi durumu okunuyor")
        job.upload_status = "published"
        job.processing_status = "published"
        job.actual_privacy_status = privacy

    def _tiktok_client(self, access_token: str) -> Any:
        """Overridable seam so tests never construct a real TikTok client."""
        return TikTokClient(access_token, self.settings)

    def _upload_video(
        self,
        job: PublishJob,
        plan: _JobPlan,
        client: YouTubeClient,
        cancel_event: threading.Event,
        report: Callable[[], None],
    ) -> None:
        youtube = plan.draft.youtube
        snippet: dict[str, object] = {
            "title": youtube.title.strip(),
            "description": youtube.description,
            "tags": list(youtube.tags),
            "categoryId": youtube.category_id,
            "defaultLanguage": youtube.default_language,
            "defaultAudioLanguage": youtube.default_audio_language,
        }
        status: dict[str, object] = {
            # A scheduled video must be private until its time comes; that is how
            # YouTube models scheduling, not a policy invented here.
            "privacyStatus": (
                "private" if plan.publish_at else youtube.privacy_status.value
            ),
            "selfDeclaredMadeForKids": youtube.made_for_kids,
            "embeddable": youtube.embeddable,
        }
        if plan.publish_at is not None:
            # RFC 3339 with a real offset. Never a naive value, never "Z" faked
            # onto a local time.
            status["publishAt"] = plan.publish_at.isoformat()

        job.phase = PublishPhase.UPLOAD_VIDEO
        job.progress = _PHASE_START[PublishPhase.UPLOAD_VIDEO]
        job.message = "Video YouTube'a yükleniyor"
        report()

        def on_progress(sent: int, total: int) -> None:
            job.uploaded_bytes = sent
            job.total_bytes = total or job.total_bytes
            fraction = (sent / total) if total else 0.0
            job.progress = min(
                _PHASE_START[PublishPhase.SET_THUMBNAIL],
                _PHASE_START[PublishPhase.UPLOAD_VIDEO] + fraction * _UPLOAD_SPAN,
            )
            job.message = (
                f"Video yükleniyor — {sent / 1_048_576:.0f} / {total / 1_048_576:.0f} MB"
            )
            report()

        response = client.upload_video(
            plan.video,
            body={"snippet": snippet, "status": status},
            notify_subscribers=youtube.notify_subscribers,
            on_progress=on_progress,
            is_cancelled=cancel_event.is_set,
        )

        video_id = str(response.get("id") or "")
        if not video_id:
            raise AppError(
                ErrorCode.YOUTUBE_UPLOAD_FAILED,
                "YouTube bir video numarası döndürmedi.",
                details="videos.insert yanıtında 'id' yok",
                http_status=502,
            )
        job.video_id = video_id
        job.video_url = f"https://youtu.be/{video_id}"
        response_status = response.get("status") or {}
        if isinstance(response_status, dict):
            job.actual_privacy_status = response_status.get("privacyStatus")
            job.upload_status = response_status.get("uploadStatus")
            published = response_status.get("publishAt")
            if published:
                job.actual_publish_at = _parse_api_datetime(str(published))
        self._persist(job)
        report()

        if (
            job.requested_publish_at is None
            and job.actual_privacy_status
            and job.actual_privacy_status != job.requested_privacy_status.value
        ):
            job.warnings.append(
                f"YouTube videoyu “{job.actual_privacy_status}” olarak kaydetti; istenen "
                f"“{job.requested_privacy_status.value}” idi. Doğrulanmamış bir Google Cloud "
                "projesi videoları gizli tutabilir."
            )

    def _set_thumbnail(
        self,
        job: PublishJob,
        plan: _JobPlan,
        client: YouTubeClient,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        if plan.thumbnail is None:
            job.thumbnail_status = AssetStatus.SKIPPED
            return
        if job.thumbnail_status is AssetStatus.UPLOADED:
            return

        advance(PublishPhase.SET_THUMBNAIL, "Kapak görseli yükleniyor")
        assert job.video_id is not None
        try:
            client.set_thumbnail(job.video_id, plan.thumbnail)
        except AppError as exc:
            # Never fatal: the video is already published. The user retries just
            # this step rather than the whole upload.
            job.thumbnail_status = AssetStatus.FAILED
            job.thumbnail_error = exc.message
            job.warnings.append(f"Kapak görseli konulamadı: {exc.message}")
            logger.warning("thumbnail for %s failed: %s", job.video_id, exc.code.value)
        else:
            job.thumbnail_status = AssetStatus.UPLOADED
            job.thumbnail_error = None
        report()

    def _upload_captions(
        self,
        job: PublishJob,
        plan: _JobPlan,
        client: YouTubeClient,
        advance: Callable[[PublishPhase, str], None],
        report: Callable[[], None],
    ) -> None:
        if plan.caption is None:
            job.caption_status = AssetStatus.SKIPPED
            return
        if job.caption_status is AssetStatus.UPLOADED:
            return

        advance(PublishPhase.UPLOAD_CAPTIONS, "Altyazı yükleniyor")
        assert job.video_id is not None
        youtube = plan.draft.youtube
        try:
            job.caption_track_id = client.insert_caption(
                job.video_id,
                plan.caption,
                language=youtube.caption_language or "en",
                name=youtube.caption_name or "English",
                is_draft=youtube.caption_is_draft,
            )
        except AppError as exc:
            job.caption_status = AssetStatus.FAILED
            job.caption_error = exc.message
            job.warnings.append(f"Altyazı eklenemedi: {exc.message}")
            logger.warning("caption for %s failed: %s", job.video_id, exc.code.value)
        else:
            job.caption_status = AssetStatus.UPLOADED
            job.caption_error = None
        report()

    def _fetch_status(
        self,
        job: PublishJob,
        client: YouTubeClient,
        advance: Callable[[PublishPhase, str], None],
    ) -> None:
        advance(PublishPhase.FETCH_STATUS, "Video durumu okunuyor")
        assert job.video_id is not None
        try:
            status = client.video_status(job.video_id)
        except AppError as exc:
            # Knowing less about a video that already uploaded is a warning.
            job.warnings.append(f"Video durumu okunamadı: {exc.message}")
            return
        _apply_status(job, status)

    def _record_history(
        self, repository: PublishingRepository, job: PublishJob, plan: _JobPlan
    ) -> PublishHistoryEntry:
        """Write (or update) this upload in the project's history, atomically."""
        existing = repository.entry_for_video(job.video_id or "", platform=job.platform.value)
        entry = PublishHistoryEntry(
            entry_id=existing.entry_id if existing else uuid.uuid4().hex[:12],
            job_id=job.id,
            project_slug=job.project_slug,
            media_id=job.media_id,
            platform=job.platform,
            filename=plan.media.filename,
            title=plan.title,
            video_id=job.video_id or "",
            video_url=job.video_url or "",
            uploaded_at=existing.uploaded_at if existing else datetime.now(timezone.utc),
            requested_publish_at=job.requested_publish_at,
            actual_publish_at=job.actual_publish_at,
            privacy_status=job.actual_privacy_status or job.requested_privacy_status.value,
            upload_status=job.upload_status,
            processing_status=job.processing_status,
            thumbnail_status=job.thumbnail_status,
            caption_status=job.caption_status,
            source=job.source,
            warnings=list(job.warnings),
        )
        repository.record_upload(entry)
        self._persist(job)
        return entry

    # --- refreshing a finished upload ------------------------------------

    def refresh_history_entry(self, slug: str, entry_id: str) -> PublishHistoryEntry:
        """Re-read one uploaded video's state from YouTube. Blocking."""
        repository = PublishingRepository(self.service.paths_for(slug))
        entry = repository.get_entry(entry_id)
        if not entry.video_id:
            return entry

        credentials = YouTubeCredentials(self.settings).usable_credentials()
        try:
            status = YouTubeClient(credentials).video_status(entry.video_id)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc, stage="status") from exc

        if status:
            entry.upload_status = status.get("uploadStatus") or entry.upload_status
            entry.processing_status = (
                status.get("processingStatus") or entry.processing_status
            )
            entry.privacy_status = status.get("privacyStatus") or entry.privacy_status
            published = status.get("publishAt")
            if published:
                entry.actual_publish_at = _parse_api_datetime(str(published))
        return repository.record_upload(entry)

    # --- bookkeeping ------------------------------------------------------

    def _finalize(
        self,
        job: PublishJob,
        status: JobStatus,
        message: str,
        running: _RunningPublish | None = None,
    ) -> None:
        job.status = status
        job.message = message
        job.finished_at = datetime.now(timezone.utc)
        job.progress = 1.0 if status is JobStatus.COMPLETED else job.progress
        job.pid = None
        self._persist(job)
        if running is not None:
            self._broadcast(running)

    def _broadcast(self, running: _RunningPublish) -> None:
        event = _event_for(running.job)
        for queue in list(running.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        if running.job.is_terminal or int(running.job.progress * 100) % 5 == 0:
            self._persist(running.job)


def _apply_status(job: PublishJob, status: dict[str, object]) -> None:
    if not status:
        return
    job.upload_status = str(status.get("uploadStatus") or "") or job.upload_status
    job.processing_status = (
        str(status.get("processingStatus") or "") or job.processing_status
    )
    privacy = status.get("privacyStatus")
    if privacy:
        job.actual_privacy_status = str(privacy)
    published = status.get("publishAt")
    if published:
        job.actual_publish_at = _parse_api_datetime(str(published))


def _parse_api_datetime(value: str) -> datetime | None:
    """Parse an RFC 3339 timestamp from the API, tolerating a trailing ``Z``."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _completion_message(job: PublishJob) -> str:
    if job.thumbnail_status is AssetStatus.FAILED or job.caption_status is AssetStatus.FAILED:
        return "Video yüklendi, bazı adımlar tamamlanamadı."
    if job.platform is not PublishingPlatform.YOUTUBE:
        return f"Video {job.platform.label}'da yayınlandı."
    if job.requested_publish_at is not None:
        return "Video yüklendi ve planlandı."
    return "Video YouTube'a yüklendi."


def _title_for(
    draft: PublishDraft, platform: PublishingPlatform, media: MediaItem
) -> str:
    """A name for the job row. YouTube has a title field; the others do not."""
    if platform is PublishingPlatform.YOUTUBE:
        return draft.youtube.title
    lines = draft.social(platform).caption.strip().splitlines()
    return (lines[0] if lines else media.filename)[:200]


def _meta_destination_note(
    platform: PublishingPlatform, target: Any, social: SocialDraft
) -> list[str]:
    """Warn when the draft's account note disagrees with where this will land.

    The ``account`` field is the user's own reminder and authorizes nothing, so
    it cannot redirect the post — but a mismatch usually means they think it is
    going somewhere else, and that is worth saying before it does not.
    """
    expected = social.account.strip().lstrip("@").casefold()
    if not expected:
        return []
    actual = (
        (target.instagram_username or "")
        if platform is PublishingPlatform.INSTAGRAM
        else target.page_name
    )
    if expected and expected != actual.strip().lstrip("@").casefold():
        return [
            f"Taslakta “{social.account}” yazıyor ama bağlı hesap “{actual}”. Gönderi bağlı "
            "hesaba gider; hesabı değiştirmek için Ayarlar'dan yeniden bağlanın."
        ]
    return []


def _event_for(job: PublishJob) -> PublishJobEvent:
    return PublishJobEvent(
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
        uploaded_bytes=job.uploaded_bytes,
        total_bytes=job.total_bytes,
        video_id=job.video_id,
        video_url=job.video_url,
        thumbnail_status=job.thumbnail_status,
        caption_status=job.caption_status,
        error_code=job.error_code,
        error_message=job.error_message,
        error_suggestion=job.error_suggestion,
    )


#: One manager per process.
_manager: PublishJobManager | None = None


def get_publish_job_manager() -> PublishJobManager:
    global _manager
    if _manager is None:
        _manager = PublishJobManager(get_settings())
    return _manager


def reset_publish_job_manager() -> None:
    """Drop the singleton. Used by tests to isolate state."""
    global _manager
    _manager = None
