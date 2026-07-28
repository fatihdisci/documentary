"""The publishing job queue.

Same contract as the render and Shorts queues — one job at a time, every state
change written to disk, progress over SSE with a polling fallback, and a job
killed mid-run reported as *interrupted* rather than sitting in "running" — but
its own queue and its own history, because an upload is not a render.

Two things are specific to uploading, and both shape the design:

* **It is network work, not CPU work.** Nothing here takes the process-wide
  FFmpeg render slot, so a video can upload while the next one renders. The
  Google client is entirely blocking, so the whole job body runs in a worker
  thread through ``anyio.to_thread.run_sync`` and never touches the event loop.

* **Half of it is not repeatable.** Once YouTube has the video, uploading it
  again creates a second video. So the id is written to the job record and to the
  project's history *the instant it exists*, and every later step — thumbnail,
  captions, status — is allowed to fail on its own. A retry of such a job resumes
  from the failed step and can never re-upload the file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio.to_thread

from app.config import Settings, get_settings
from app.errors import AppError, ConflictError, ErrorCode, NotFoundError
from app.models.enums import JobStatus
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
    SourceFingerprint,
)
from app.publishing.repository import PublishingRepository
from app.publishing.service import (
    PublishingService,
    resolve_publish_at,
    sha256_file,
)
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

#: Where each phase sits on the overall progress bar. The upload owns most of it
#: because it is the only part whose duration depends on the file.
_PHASE_START = {
    PublishPhase.VALIDATE: 0.0,
    PublishPhase.AUTHENTICATE: 0.03,
    PublishPhase.HASH_SOURCE: 0.06,
    PublishPhase.UPLOAD_VIDEO: 0.12,
    PublishPhase.SET_THUMBNAIL: 0.84,
    PublishPhase.UPLOAD_CAPTIONS: 0.90,
    PublishPhase.FETCH_STATUS: 0.96,
    PublishPhase.COMPLETE: 1.0,
}
_UPLOAD_SPAN = _PHASE_START[PublishPhase.SET_THUMBNAIL] - _PHASE_START[PublishPhase.UPLOAD_VIDEO]


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
        self._bind_loop()
        self.ensure_history()
        assert self._lock is not None and self._queue is not None

        media, draft, _video, warnings = self.service.prepare_upload(
            slug, request.media_id, allow_duplicate=request.allow_duplicate
        )

        async with self._lock:
            existing = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.is_active
                    and job.project_slug == slug
                    and job.media_id == request.media_id
                ),
                None,
            )
            if existing is not None:
                raise ConflictError(
                    ErrorCode.YOUTUBE_UPLOAD_FAILED,
                    "Bu dosya için zaten bir yükleme sürüyor.",
                    details=f"işlem numarası {existing.id}",
                    suggestion="Bitmesini bekleyin ya da onu iptal edip yeniden başlatın.",
                )

            job = PublishJob(
                project_slug=slug,
                media_id=request.media_id,
                platform=PublishingPlatform.YOUTUBE,
                source=media.fingerprint,
                title=draft.youtube.title,
                requested_privacy_status=draft.youtube.privacy_status,
                total_bytes=media.size_bytes,
                warnings=list(warnings),
                pid=os.getpid(),
            )
            if draft.youtube.publish_mode is PublishMode.SCHEDULE:
                job.requested_publish_at = resolve_publish_at(draft.youtube.publish_at_local)
            self._jobs[job.id] = job
            self._persist(job)
            await self._queue.put(job.id)

        logger.info("queued YouTube upload %s for %s (%s)", job.id, slug, request.media_id)
        await self.start()
        return job

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
                title=previous.title,
                requested_privacy_status=previous.requested_privacy_status,
                requested_publish_at=previous.requested_publish_at,
                total_bytes=previous.total_bytes,
                uploaded_bytes=previous.total_bytes,
                video_id=previous.video_id,
                video_url=previous.video_url,
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

        return await self.submit_youtube(
            previous.project_slug,
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

        thumbnail = (
            repository.thumbnail_path(draft.youtube.thumbnail_file)
            if draft.youtube.thumbnail_file
            else None
        )
        caption = None
        if draft.youtube.upload_captions and draft.youtube.caption_file:
            caption = self.service.caption_path(
                job.project_slug, draft.youtube.caption_file, draft.youtube.caption_source
            )
        publish_at = (
            resolve_publish_at(draft.youtube.publish_at_local)
            if draft.youtube.publish_mode is PublishMode.SCHEDULE
            else None
        )
        return _JobPlan(
            media=media,
            draft=draft,
            video=self.service.media_path(job.project_slug, job.media_id),
            thumbnail=thumbnail,
            caption=caption,
            publish_at=publish_at,
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
            job.progress = _PHASE_START[phase]
            job.message = message
            report()

        def check_cancel() -> None:
            if cancel_event.is_set():
                raise UploadCancelled()

        advance(PublishPhase.VALIDATE, "Dosya ve bilgiler kontrol ediliyor")
        check_cancel()

        advance(PublishPhase.AUTHENTICATE, "YouTube bağlantısı doğrulanıyor")
        credentials = YouTubeCredentials(self.settings).usable_credentials()
        client = YouTubeClient(credentials)
        check_cancel()

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
            check_cancel()

            self._upload_video(job, plan, client, cancel_event, report)
            # The video now exists on the channel. Record it before anything
            # else runs, so a crash one line later cannot cause a second upload.
            self._record_history(repository, job, plan)

        self._set_thumbnail(job, plan, client, advance, report)
        self._upload_captions(job, plan, client, advance, report)
        self._fetch_status(job, client, advance)

        job.phase = PublishPhase.COMPLETE
        job.progress = 1.0
        self._record_history(repository, job, plan)
        report()

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
        existing = repository.entry_for_video(job.video_id or "")
        entry = PublishHistoryEntry(
            entry_id=existing.entry_id if existing else uuid.uuid4().hex[:12],
            job_id=job.id,
            project_slug=job.project_slug,
            media_id=job.media_id,
            platform=job.platform,
            filename=plan.media.filename,
            title=plan.draft.youtube.title,
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
    if job.requested_publish_at is not None:
        return "Video yüklendi ve planlandı."
    return "Video YouTube'a yüklendi."


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
