"""Shorts endpoints: sources, timeline, preflight, build, monitor, download.

Deliberately a separate router from ``api/render.py``. Nothing here touches a
long render's endpoints, job records or export listing; the only shared surface
is the project itself and the exports folder, which Shorts read from and never
write to.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.errors import ErrorCode, NotFoundError
from app.shorts.jobs import get_short_job_manager
from app.shorts.models import (
    ShortJob,
    ShortRecord,
    ShortRequest,
    ShortSourceRender,
    ShortSourceTimeline,
    ShortsPreflightResponse,
)
from app.shorts.service import ShortsService

logger = logging.getLogger("evb.api.shorts")

router = APIRouter(prefix="/api/projects/{slug}/shorts", tags=["shorts"])
jobs_router = APIRouter(prefix="/api/short-jobs", tags=["shorts"])


def service() -> ShortsService:
    return ShortsService()


# --- sources ---------------------------------------------------------------


@router.get("/sources", response_model=list[ShortSourceRender])
def list_sources(slug: str) -> list[ShortSourceRender]:
    """Completed long renders that can be cut into a Short."""
    return service().list_sources(slug)


@router.get("/sources/{render_id}/timeline", response_model=ShortSourceTimeline)
def source_timeline(slug: str, render_id: str) -> ShortSourceTimeline:
    """The section cards for one source: intro 0, scenes 1..N, outro N+1."""
    return service().timeline(slug, render_id)


@router.get("/sources/{render_id}/poster")
def source_poster(slug: str, render_id: str) -> FileResponse:
    """A cached thumbnail for the source picker."""
    path = service().source_poster(slug, render_id)
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@router.get("/frames/{filename}")
def preview_frame(slug: str, filename: str) -> FileResponse:
    """A cached preview frame. Confined to the project's Shorts cache."""
    path = service().frame_path(slug, filename)
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


# --- planning and building --------------------------------------------------


@router.post("/preflight", response_model=ShortsPreflightResponse)
def preflight(slug: str, request: ShortRequest) -> ShortsPreflightResponse:
    """Report the plan, the duration, the warnings and any blocking issue."""
    return service().preflight(slug, request)


@router.post("", response_model=ShortJob, status_code=202)
async def create_short(slug: str, request: ShortRequest) -> ShortJob:
    """Queue a Short, or hand back the identical one that already exists."""
    return await get_short_job_manager().submit(slug, request)


@router.get("", response_model=list[ShortRecord])
def list_shorts(slug: str) -> list[ShortRecord]:
    """Every finished Short in this project, newest first."""
    return service().list_shorts(slug)


@router.get("/jobs", response_model=list[ShortJob])
def project_short_jobs(slug: str, limit: int = Query(default=25, ge=1, le=200)) -> list[ShortJob]:
    service().paths_for(slug)  # 404s if the project is gone
    return get_short_job_manager().list_jobs(project_slug=slug, limit=limit)


@router.get("/exports/{filename}")
def download_short(slug: str, filename: str) -> FileResponse:
    """Serve a finished Short. Paths are confined to exports/shorts."""
    path = service().export_path(slug, filename)
    return FileResponse(path, filename=path.name)


@router.delete("/{short_id}")
def delete_short(slug: str, short_id: str) -> dict:
    """Delete one Short: its MP4, side-cars and any cut nothing else needs."""
    return service().delete_short(slug, short_id)


# --- jobs -------------------------------------------------------------------


@jobs_router.get("", response_model=list[ShortJob])
def list_jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[ShortJob]:
    return get_short_job_manager().list_jobs(limit=limit)


@jobs_router.get("/active", response_model=ShortJob | None)
def active_job(slug: str | None = Query(default=None)) -> ShortJob | None:
    return get_short_job_manager().active_job(project_slug=slug)


@jobs_router.get("/{job_id}", response_model=ShortJob)
def get_job(job_id: str) -> ShortJob:
    return get_short_job_manager().get(job_id)


@jobs_router.post("/{job_id}/cancel", response_model=ShortJob)
async def cancel_job(job_id: str) -> ShortJob:
    return await get_short_job_manager().cancel(job_id)


@jobs_router.post("/{job_id}/retry", response_model=ShortJob, status_code=202)
async def retry_job(job_id: str) -> ShortJob:
    return await get_short_job_manager().retry(job_id)


@jobs_router.get("/{job_id}/log")
def job_log(job_id: str) -> FileResponse:
    manager = get_short_job_manager()
    job = manager.get(job_id)
    if not job.log_file:
        raise NotFoundError(
            ErrorCode.SHORT_JOB_NOT_FOUND,
            "Bu kısa videonun kayıt dosyası yok.",
            suggestion="Kayıt dosyası, kısa video kaydedilme aşamasına gelince oluşur.",
        )
    path = ShortsService().export_path(job.project_slug, job.log_file)
    return FileResponse(path, media_type="text/plain", filename=path.name)


@jobs_router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events carrying live progress for one Short."""
    manager = get_short_job_manager()
    manager.get(job_id)  # 404 early rather than inside the stream

    async def stream():  # noqa: ANN202
        try:
            async for event in manager.subscribe(job_id):
                payload = json.dumps(event.model_dump(mode="json", by_alias=True))
                yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001 - never leave the client hanging
            logger.exception("SSE stream for Short %s failed", job_id)
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
