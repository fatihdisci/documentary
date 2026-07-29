"""Publishing endpoints: connections, media, drafts, assets, uploads, history.

Split into four routers for the same reason the rest of the API is:

* ``/api/publishing/youtube`` — the YouTube account connection, which is per
  computer, not per project.
* ``/api/publishing/meta`` and ``/api/publishing/tiktok`` — the same, for the
  other three destinations. Meta is one connection serving two platforms.
* ``/api/projects/{slug}/publishing`` — everything that belongs to one project.
* ``/api/publishing/jobs`` — one upload's live progress, by job id.

Every OAuth and network operation is blocking, so it goes through
``anyio.to_thread`` rather than stalling the event loop; and no response on any
of these routes carries a client id, client secret, app secret or token — see
``publishing/youtube.py``, ``publishing/meta.py`` and ``publishing/tiktok.py``.
"""

from __future__ import annotations

import json
import logging

import anyio.to_thread
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from app.config import get_settings
from app.errors import ErrorCode, ValidationError
from app.models.base import CamelModel
from app.publishing.hosting import (
    ACCESS_KEY_SECRET,
    SECRET_KEY_SECRET,
    media_host_status,
)
from app.publishing.jobs import get_publish_job_manager
from app.publishing.meta import MetaCredentials
from app.publishing.models import (
    ClientSecretUploadResponse,
    DraftResponse,
    MediaHostStatus,
    MediaItem,
    MetaAppCredentials,
    MetaConnection,
    MetaPageSelection,
    OAuthStart,
    ObjectStorageSettings,
    PublishDraft,
    PublishHistoryEntry,
    PublishJob,
    PublishRequest,
    PublishingPlatform,
    TikTokAppCredentials,
    TikTokConnection,
    YouTubeConnection,
)
from app.publishing.service import PublishingService
from app.publishing.tiktok import TikTokCredentials
from app.publishing.youtube import YouTubeCredentials

logger = logging.getLogger("evb.api.publishing")

router = APIRouter(prefix="/api/publishing/youtube", tags=["publishing"])
meta_router = APIRouter(prefix="/api/publishing/meta", tags=["publishing"])
tiktok_router = APIRouter(prefix="/api/publishing/tiktok", tags=["publishing"])
hosting_router = APIRouter(prefix="/api/publishing/media-host", tags=["publishing"])
project_router = APIRouter(prefix="/api/projects/{slug}/publishing", tags=["publishing"])
jobs_router = APIRouter(prefix="/api/publishing/jobs", tags=["publishing"])


def service() -> PublishingService:
    return PublishingService()


def credentials() -> YouTubeCredentials:
    return YouTubeCredentials()


def meta_credentials() -> MetaCredentials:
    return MetaCredentials()


def tiktok_credentials() -> TikTokCredentials:
    return TikTokCredentials()


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


# --- Meta -------------------------------------------------------------------


@meta_router.get("/status", response_model=MetaConnection)
async def meta_status() -> MetaConnection:
    """Connection state. Contains no App ID, App Secret or access token."""
    store = meta_credentials()
    return await anyio.to_thread.run_sync(store.status)


@meta_router.post("/app-credentials", response_model=MetaConnection)
async def set_meta_app_credentials(payload: MetaAppCredentials) -> MetaConnection:
    """Store the App ID and App Secret. Neither is ever readable again.

    Write-only on purpose: the response reports *presence*, and no endpoint
    exists that returns either value.
    """
    store = meta_credentials()
    await anyio.to_thread.run_sync(
        lambda: store.store_app_credentials(
            payload.app_id, payload.app_secret, replace=payload.replace
        )
    )
    return await anyio.to_thread.run_sync(store.status)


@meta_router.delete("/app-credentials", response_model=MetaConnection)
async def clear_meta_app_credentials() -> MetaConnection:
    """Forget the app credentials and the grant that depends on them."""
    store = meta_credentials()
    await anyio.to_thread.run_sync(store.forget_app_credentials)
    return await anyio.to_thread.run_sync(store.status)


@meta_router.post("/connect", response_model=OAuthStart)
async def start_meta_connect() -> OAuthStart:
    """The URL to open in the user's browser. Meta redirects back to us."""
    store = meta_credentials()
    return await anyio.to_thread.run_sync(store.start_authorization)


@meta_router.get("/callback", response_class=HTMLResponse)
async def meta_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Where Meta sends the browser back.

    Returns a page, not JSON: a human is looking at it. The panel finds out by
    re-reading the status, so nothing here has to talk to the frontend.
    """
    if error or not code or not state:
        return _callback_page(
            "Meta bağlantısı tamamlanmadı",
            error_description or error or "Yetkilendirme kodu alınamadı.",
            ok=False,
        )
    store = meta_credentials()
    try:
        connection = await anyio.to_thread.run_sync(
            lambda: store.complete_authorization(code, state)
        )
    except Exception as exc:  # noqa: BLE001 - the browser must get a readable page
        logger.warning("Meta callback failed: %s", type(exc).__name__)
        message = getattr(exc, "message", None) or "Bağlantı tamamlanamadı."
        return _callback_page("Meta bağlantısı tamamlanmadı", str(message), ok=False)
    return _callback_page("Meta bağlantısı kuruldu", connection.status_message, ok=True)


@meta_router.post("/page", response_model=MetaConnection)
async def select_meta_page(selection: MetaPageSelection) -> MetaConnection:
    """Choose which Facebook Page (and its Instagram account) to publish to."""
    store = meta_credentials()
    return await anyio.to_thread.run_sync(lambda: store.select_page(selection.page_id))


@meta_router.delete("/disconnect", response_model=MetaConnection)
async def disconnect_meta() -> MetaConnection:
    """Delete the stored grant. The App ID and App Secret are kept."""
    store = meta_credentials()
    return await anyio.to_thread.run_sync(store.disconnect)


# --- TikTok -----------------------------------------------------------------


@tiktok_router.get("/status", response_model=TikTokConnection)
async def tiktok_status(refresh: bool = Query(default=False)) -> TikTokConnection:
    store = tiktok_credentials()
    return await anyio.to_thread.run_sync(lambda: store.status(refresh_creator=refresh))


@tiktok_router.post("/app-credentials", response_model=TikTokConnection)
async def set_tiktok_app_credentials(payload: TikTokAppCredentials) -> TikTokConnection:
    store = tiktok_credentials()
    await anyio.to_thread.run_sync(
        lambda: store.store_app_credentials(
            payload.client_key, payload.client_secret, replace=payload.replace
        )
    )
    return await anyio.to_thread.run_sync(lambda: store.status(refresh_creator=False))


@tiktok_router.delete("/app-credentials", response_model=TikTokConnection)
async def clear_tiktok_app_credentials() -> TikTokConnection:
    store = tiktok_credentials()
    await anyio.to_thread.run_sync(store.forget_app_credentials)
    return await anyio.to_thread.run_sync(lambda: store.status(refresh_creator=False))


@tiktok_router.post("/connect", response_model=OAuthStart)
async def start_tiktok_connect() -> OAuthStart:
    store = tiktok_credentials()
    return await anyio.to_thread.run_sync(store.start_authorization)


@tiktok_router.get("/callback", response_class=HTMLResponse)
async def tiktok_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    if error or not code or not state:
        return _callback_page(
            "TikTok bağlantısı tamamlanmadı",
            error_description or error or "Yetkilendirme kodu alınamadı.",
            ok=False,
        )
    store = tiktok_credentials()
    try:
        connection = await anyio.to_thread.run_sync(
            lambda: store.complete_authorization(code, state)
        )
    except Exception as exc:  # noqa: BLE001 - the browser must get a readable page
        logger.warning("TikTok callback failed: %s", type(exc).__name__)
        message = getattr(exc, "message", None) or "Bağlantı tamamlanamadı."
        return _callback_page("TikTok bağlantısı tamamlanmadı", str(message), ok=False)
    return _callback_page("TikTok bağlantısı kuruldu", connection.status_message, ok=True)


@tiktok_router.delete("/disconnect", response_model=TikTokConnection)
async def disconnect_tiktok() -> TikTokConnection:
    store = tiktok_credentials()
    return await anyio.to_thread.run_sync(store.disconnect)


# --- temporary media hosting ------------------------------------------------


@hosting_router.get("/status", response_model=MediaHostStatus)
def hosting_status() -> MediaHostStatus:
    """Bucket coordinates and whether the keys are present. Never the keys."""
    return media_host_status()


@hosting_router.put("/settings", response_model=MediaHostStatus)
def save_hosting_settings(payload: ObjectStorageSettings) -> MediaHostStatus:
    """Save the bucket, and the keys when new ones were supplied.

    Empty key fields leave the stored pair alone, so saving a corrected bucket
    name does not silently wipe working credentials.
    """
    settings = get_settings()
    provider = (payload.provider or "none").strip().lower()
    if provider not in {"none", "s3", "r2"}:
        raise ValidationError(
            ErrorCode.MEDIA_HOST_NOT_CONFIGURED,
            f"'{payload.provider}' tanınmayan bir barındırma türü.",
            details="desteklenenler: none, s3, r2",
        )
    if provider != "none" and payload.endpoint and not payload.endpoint.startswith("https://"):
        raise ValidationError(
            ErrorCode.MEDIA_HOST_NOT_CONFIGURED,
            "Endpoint adresi https:// ile başlamalı.",
            details=f"girilen: {payload.endpoint}",
            suggestion="Meta yalnızca güvenli bağlantılardan video indirir.",
        )

    settings.save_mutable(
        settings.mutable.model_copy(
            update={
                "media_host_provider": provider,
                "object_storage_endpoint": payload.endpoint.strip().rstrip("/"),
                "object_storage_bucket": payload.bucket.strip(),
                "object_storage_region": (payload.region or "auto").strip(),
                "object_storage_prefix": payload.prefix.strip("/"),
                "media_host_ttl_seconds": max(300, min(604800, payload.ttl_seconds)),
                "media_host_delete_after_publish": payload.delete_after_publish,
            }
        )
    )
    if payload.access_key_id:
        settings.set_secret(ACCESS_KEY_SECRET, payload.access_key_id.strip())
    if payload.secret_access_key:
        settings.set_secret(SECRET_KEY_SECRET, payload.secret_access_key.strip())
    return media_host_status(settings)


@hosting_router.delete("/keys", response_model=MediaHostStatus)
def clear_hosting_keys() -> MediaHostStatus:
    settings = get_settings()
    settings.set_secret(ACCESS_KEY_SECRET, None)
    settings.set_secret(SECRET_KEY_SECRET, None)
    return media_host_status(settings)


def _callback_page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    """A small self-contained page for the OAuth redirect to land on."""
    colour = "#1c7c4a" if ok else "#a12b2b"
    body = (
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{font-family:system-ui,-apple-system,sans-serif;margin:0;display:flex;"
        "min-height:100vh;align-items:center;justify-content:center;background:#f6f6f4}"
        f"main{{max-width:32rem;padding:2rem;text-align:center}}h1{{color:{colour};"
        "font-size:1.25rem}p{color:#444;line-height:1.6}"
        "</style></head><body><main>"
        f"<h1>{title}</h1><p>{message}</p>"
        "<p>Bu sekmeyi kapatıp uygulamaya dönebilirsiniz.</p>"
        "</main></body></html>"
    )
    return HTMLResponse(body, status_code=200 if ok else 400)


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


@project_router.post("/instagram", response_model=PublishJob, status_code=202)
async def publish_to_instagram(slug: str, request: PublishRequest) -> PublishJob:
    """Queue one Instagram Reel. Its own job, its own duplicate protection."""
    return await get_publish_job_manager().submit(
        slug, PublishingPlatform.INSTAGRAM, request
    )


@project_router.post("/facebook", response_model=PublishJob, status_code=202)
async def publish_to_facebook(slug: str, request: PublishRequest) -> PublishJob:
    """Queue one Facebook Page Reel. Independent of the Instagram job."""
    return await get_publish_job_manager().submit(
        slug, PublishingPlatform.FACEBOOK, request
    )


@project_router.post("/tiktok", response_model=PublishJob, status_code=202)
async def publish_to_tiktok(slug: str, request: PublishRequest) -> PublishJob:
    """Queue one TikTok Direct Post."""
    return await get_publish_job_manager().submit(slug, PublishingPlatform.TIKTOK, request)


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
