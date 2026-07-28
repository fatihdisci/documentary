"""Publishing endpoints: connection, media, drafts, assets, uploads, history.

Split into three routers for the same reason the rest of the API is:

* ``/api/publishing/youtube`` — the account connection, which is per computer,
  not per project.
* ``/api/projects/{slug}/publishing`` — everything that belongs to one project.
* ``/api/publishing/jobs`` — one upload's live progress, by job id.

Every OAuth operation is blocking, so it goes through ``anyio.to_thread`` rather
than stalling the event loop; and no response on any of these routes carries a
client id, client secret or token — see ``publishing/youtube.py``.
"""

from __future__ import annotations

import json
import logging

import anyio.to_thread
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_settings
from app.errors import ErrorCode, ValidationError
from app.models.base import CamelModel
from app.publishing.jobs import get_publish_job_manager
from app.publishing.models import (
    ClientSecretUploadResponse,
    DraftResponse,
    MediaItem,
    PublishDraft,
    PublishHistoryEntry,
    PublishJob,
    PublishRequest,
    YouTubeConnection,
)
from app.publishing.service import PublishingService
from app.publishing.youtube import YouTubeCredentials

logger = logging.getLogger("evb.api.publishing")

router = APIRouter(prefix="/api/publishing/youtube", tags=["publishing"])
project_router = APIRouter(prefix="/api/projects/{slug}/publishing", tags=["publishing"])
jobs_router = APIRouter(prefix="/api/publishing/jobs", tags=["publishing"])


def service() -> PublishingService:
    return PublishingService()


def credentials() -> YouTubeCredentials:
    return YouTubeCredentials()


class AssetUploadResponse(CamelModel):
    """Where a stored asset ended up. A filename, never a path."""

    filename: str
    url: str


class ClientFileSelection(CamelModel):
    """Which installed OAuth client file to use, by basename."""

    file_name: str


# --- connection -------------------------------------------------------------


@router.get("/status", response_model=YouTubeConnection)
async def youtube_status(refresh: bool = Query(default=False)) -> YouTubeConnection:
    """Connection state. Contains no credential of any kind."""
    store = credentials()
    return await anyio.to_thread.run_sync(lambda: store.status(refresh_channel=refresh))


@router.post("/client-secret", response_model=ClientSecretUploadResponse, status_code=201)
async def upload_client_secret(
    file: UploadFile = File(...),
) -> ClientSecretUploadResponse:
    """Install a Desktop-app OAuth client file after validating its structure."""
    settings = get_settings()
    limit = 1 * 1_048_576  # an OAuth client file is a few hundred bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationError(
            ErrorCode.YOUTUBE_CLIENT_INVALID,
            "Bu dosya bir OAuth istemci dosyası için fazla büyük.",
            details=f"limit: {limit} bayt",
        )

    store = credentials()
    name = await anyio.to_thread.run_sync(
        store.store_client_file, data, file.filename or "client_secret.json"
    )
    # Remember the choice so a folder with several files stays predictable.
    mutable = settings.mutable.model_copy(update={"youtube_client_secret_file": name})
    settings.save_mutable(mutable)
    connection = await anyio.to_thread.run_sync(lambda: store.status(refresh_channel=False))
    return ClientSecretUploadResponse(connection=connection, stored_file_name=name)


@router.post("/client-secret/select", response_model=YouTubeConnection)
async def select_client_secret(selection: ClientFileSelection) -> YouTubeConnection:
    """Pick which of several installed client files to use."""
    settings = get_settings()
    store = credentials()
    available = await anyio.to_thread.run_sync(store.available_client_files)
    if selection.file_name not in available:
        raise ValidationError(
            ErrorCode.YOUTUBE_CLIENT_MISSING,
            f"'{selection.file_name}' adlı bir OAuth istemci dosyası bulunamadı.",
            details="kullanılabilir dosyalar: " + (", ".join(available) or "yok"),
        )
    settings.save_mutable(
        settings.mutable.model_copy(update={"youtube_client_secret_file": selection.file_name})
    )
    return await anyio.to_thread.run_sync(lambda: store.status(refresh_channel=True))


@router.post("/connect", response_model=YouTubeConnection)
async def connect_youtube() -> YouTubeConnection:
    """Run the desktop OAuth flow in the user's browser and store the grant."""
    store = credentials()
    return await anyio.to_thread.run_sync(store.connect)


@router.delete("/disconnect", response_model=YouTubeConnection)
async def disconnect_youtube() -> YouTubeConnection:
    """Delete the stored token. The installed client file is kept."""
    store = credentials()
    return await anyio.to_thread.run_sync(store.disconnect)


# --- media and drafts -------------------------------------------------------


@project_router.get("/media", response_model=list[MediaItem])
def list_media(
    slug: str, include_unrecommended: bool = Query(default=True)
) -> list[MediaItem]:
    """Long videos and Shorts that exist on disk and can be published."""
    return service().list_media(slug, include_unrecommended=include_unrecommended)


@project_router.get("/drafts/{media_id:path}", response_model=DraftResponse)
def get_draft(slug: str, media_id: str) -> DraftResponse:
    """The stored draft, or a fresh one seeded from the project's metadata."""
    return service().get_draft(slug, media_id)


@project_router.put("/drafts/{media_id:path}", response_model=DraftResponse)
def save_draft(slug: str, media_id: str, draft: PublishDraft) -> DraftResponse:
    return service().save_draft(slug, media_id, draft)


# --- assets -----------------------------------------------------------------


@project_router.post("/assets/thumbnail", response_model=AssetUploadResponse, status_code=201)
async def upload_thumbnail(slug: str, file: UploadFile = File(...)) -> AssetUploadResponse:
    """Store a JPEG/PNG thumbnail, validated from its content."""
    data = await _read_upload(file, limit_bytes=4 * 1_048_576)
    name = service().store_thumbnail(slug, data, file.filename or "thumbnail.jpg")
    return AssetUploadResponse(
        filename=name,
        url=f"/api/projects/{slug}/publishing/assets/thumbnail/{name}",
    )


@project_router.get("/assets/thumbnail/{filename}")
def serve_thumbnail(slug: str, filename: str) -> FileResponse:
    path = service().thumbnail_path(slug, filename)
    return FileResponse(path, filename=path.name)


@project_router.post("/assets/caption", response_model=AssetUploadResponse, status_code=201)
async def upload_caption(slug: str, file: UploadFile = File(...)) -> AssetUploadResponse:
    """Store an English .srt, validated from its content."""
    data = await _read_upload(file, limit_bytes=4 * 1_048_576)
    name = service().store_caption(slug, data, file.filename or "captions.srt")
    return AssetUploadResponse(
        filename=name,
        url=f"/api/projects/{slug}/publishing/assets/caption/{name}",
    )


@project_router.get("/assets/caption/{filename}")
def serve_caption(slug: str, filename: str) -> FileResponse:
    path = service().caption_path(slug, filename, "asset")
    return FileResponse(path, media_type="text/plain", filename=path.name)


# --- uploading --------------------------------------------------------------


@project_router.post("/youtube", response_model=PublishJob, status_code=202)
async def publish_to_youtube(slug: str, request: PublishRequest) -> PublishJob:
    """Queue one YouTube upload for a media file in this project."""
    return await get_publish_job_manager().submit_youtube(slug, request)


@project_router.get("/history", response_model=list[PublishHistoryEntry])
def publish_history(slug: str) -> list[PublishHistoryEntry]:
    """Every video from this project that reached a platform, newest first."""
    return service().history(slug)


@project_router.post("/history/{entry_id}/refresh", response_model=PublishHistoryEntry)
async def refresh_history_entry(slug: str, entry_id: str) -> PublishHistoryEntry:
    """Re-read one uploaded video's processing and privacy state from YouTube."""
    manager = get_publish_job_manager()
    return await anyio.to_thread.run_sync(manager.refresh_history_entry, slug, entry_id)


@project_router.get("/jobs", response_model=list[PublishJob])
def project_jobs(slug: str, limit: int = Query(default=25, ge=1, le=200)) -> list[PublishJob]:
    service().paths_for(slug)  # 404s if the project is gone
    return get_publish_job_manager().list_jobs(project_slug=slug, limit=limit)


# --- jobs -------------------------------------------------------------------


@jobs_router.get("", response_model=list[PublishJob])
def list_jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[PublishJob]:
    return get_publish_job_manager().list_jobs(limit=limit)


@jobs_router.get("/active", response_model=PublishJob | None)
def active_job(slug: str | None = Query(default=None)) -> PublishJob | None:
    return get_publish_job_manager().active_job(project_slug=slug)


@jobs_router.get("/{job_id}", response_model=PublishJob)
def get_job(job_id: str) -> PublishJob:
    return get_publish_job_manager().get(job_id)


@jobs_router.post("/{job_id}/cancel", response_model=PublishJob)
async def cancel_job(job_id: str) -> PublishJob:
    return await get_publish_job_manager().cancel(job_id)


@jobs_router.post("/{job_id}/retry", response_model=PublishJob, status_code=202)
async def retry_job(job_id: str) -> PublishJob:
    return await get_publish_job_manager().retry(job_id)


@jobs_router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events carrying live progress for one upload."""
    manager = get_publish_job_manager()
    manager.get(job_id)  # 404 early rather than inside the stream

    async def stream():  # noqa: ANN202
        try:
            async for event in manager.subscribe(job_id):
                payload = json.dumps(event.model_dump(mode="json", by_alias=True))
                yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001 - never leave the client hanging
            logger.exception("SSE stream for upload %s failed", job_id)
            error = json.dumps({"error": str(exc), "jobId": job_id})
            yield f"event: error\ndata: {error}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _read_upload(file: UploadFile, *, limit_bytes: int) -> bytes:
    """Read an upload without buffering more than the limit allows."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > limit_bytes:
            raise ValidationError(
                ErrorCode.FILE_TOO_LARGE,
                f"'{file.filename}' çok büyük.",
                details=f"limit: {limit_bytes} bayt",
                suggestion="Dosyayı küçültüp tekrar yükleyin.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
