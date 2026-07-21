"""Render endpoints: submit, monitor, cancel, retry, download."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.projects import repo
from app.config import get_settings
from app.errors import ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.enums import QualityPreset
from app.models.jobs import RenderJob
from app.render.codecs import estimate_disk_mb
from app.render.jobs import get_job_manager
from app.render.pipeline import transition_summary
from app.storage.paths import safe_join
from app.timing.schedule import build_timeline, duration_summary

logger = logging.getLogger("evb.api.render")

router = APIRouter(prefix="/api/projects/{slug}", tags=["render"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["render"])


class RenderRequest(CamelModel):
    quality: QualityPreset | None = None


class PreflightResponse(CamelModel):
    """Everything the user should see before committing to a render."""

    ready: bool
    blocking_issues: list[str] = []
    warnings: list[str] = []
    timing: dict = {}
    disk: dict = {}
    transitions: list[dict] = []
    estimated_render_seconds: float = 0.0


@router.get("/render/preflight", response_model=PreflightResponse)
def preflight(slug: str) -> PreflightResponse:
    """Report what will happen, and what would stop it, without rendering."""
    import shutil

    repository = repo()
    project = repository.load(slug)
    paths = repository.paths_for(slug)
    settings = get_settings()

    blocking: list[str] = []
    warnings: list[str] = []

    if not project.active_scenes:
        blocking.append("This project has no enabled scenes.")

    for index, scene in enumerate(project.active_scenes, start=1):
        if not scene.image_file:
            blocking.append(f"Scene {index} has no image.")
        if scene.narration.strip() and not scene.audio_file:
            blocking.append(f"Scene {index} has narration but no audio yet.")
        if not scene.narration.strip():
            warnings.append(f"Scene {index} has no narration; it will be held silently.")

    timing: dict = {}
    disk: dict = {}
    estimated_seconds = 0.0

    try:
        timeline = build_timeline(project, validate=False)
        timing = duration_summary(timeline, project)
        warnings.extend(timeline.warnings)

        disk = estimate_disk_mb(
            duration_seconds=timeline.total_duration_seconds,
            scene_count=len(timeline.entries),
            intermediate=project.export.intermediate_codec,
            quality=project.export.quality,
            hardware=project.export.use_hardware_encoder,
        )
        try:
            free_mb = shutil.disk_usage(paths.root).free / 1_048_576
            disk["freeMb"] = round(free_mb, 1)
            needed = disk["totalMb"] + settings.mutable.disk_safety_margin_mb
            disk["sufficient"] = free_mb >= needed
            if not disk["sufficient"]:
                blocking.append(
                    f"Not enough disk space: about {disk['totalMb'] / 1024:.1f} GB is "
                    f"needed but only {free_mb / 1024:.1f} GB is free."
                )
        except OSError:
            disk["freeMb"] = -1.0
            disk["sufficient"] = True

        # Roughly 5x realtime cold on a laptop; cached clips make it far faster.
        estimated_seconds = round(timeline.total_duration_seconds * 5.0, 0)

    except Exception as exc:  # noqa: BLE001 - preflight must never itself fail
        blocking.append(str(exc))

    return PreflightResponse(
        ready=not blocking,
        blocking_issues=blocking,
        warnings=warnings,
        timing=timing,
        disk=disk,
        transitions=transition_summary(project),
        estimated_render_seconds=estimated_seconds,
    )


@router.post("/render", response_model=RenderJob, status_code=202)
async def start_render(slug: str, request: RenderRequest | None = None) -> RenderJob:
    manager = get_job_manager()
    return await manager.submit(slug, quality=(request.quality if request else None))


@router.get("/renders", response_model=list[RenderJob])
def project_history(slug: str, limit: int = Query(default=25, ge=1, le=200)) -> list[RenderJob]:
    repo().load(slug)
    return get_job_manager().list_jobs(project_slug=slug, limit=limit)


@router.get("/exports/{filename}")
def download_export(slug: str, filename: str) -> FileResponse:
    """Serve a finished export. Paths are confined to the project's folder."""
    repository = repo()
    repository.load(slug)
    paths = repository.paths_for(slug)

    target = safe_join(paths.exports, filename)
    if not target.is_file():
        # Per-scene subtitles live in a subdirectory.
        for candidate in paths.exports.rglob(filename):
            if candidate.is_file():
                target = candidate
                break
        else:
            raise NotFoundError(
                ErrorCode.MISSING_IMAGE,
                f"'{filename}' is not in this project's exports.",
                suggestion="Refresh the render history; the file may have been deleted.",
            )
    return FileResponse(target, filename=target.name)


@router.get("/exports")
def list_exports(slug: str) -> list[dict]:
    """Every finished export on disk, newest first."""
    repository = repo()
    repository.load(slug)
    paths = repository.paths_for(slug)
    if not paths.exports.is_dir():
        return []

    entries = [
        {
            "filename": path.name,
            "sizeBytes": path.stat().st_size,
            "modifiedAt": path.stat().st_mtime,
            "url": f"/api/projects/{slug}/exports/{path.name}",
        }
        for path in paths.exports.iterdir()
        if path.is_file()
    ]
    entries.sort(key=lambda e: e["modifiedAt"], reverse=True)
    return entries


# --- jobs -------------------------------------------------------------------


@jobs_router.get("", response_model=list[RenderJob])
def list_jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[RenderJob]:
    return get_job_manager().list_jobs(limit=limit)


@jobs_router.get("/active", response_model=RenderJob | None)
def active_job() -> RenderJob | None:
    return get_job_manager().active_job()


@jobs_router.get("/{job_id}", response_model=RenderJob)
def get_job(job_id: str) -> RenderJob:
    return get_job_manager().get(job_id)


@jobs_router.post("/{job_id}/cancel", response_model=RenderJob)
async def cancel_job(job_id: str) -> RenderJob:
    return await get_job_manager().cancel(job_id)


@jobs_router.post("/{job_id}/retry", response_model=RenderJob, status_code=202)
async def retry_job(job_id: str) -> RenderJob:
    return await get_job_manager().retry(job_id)


@jobs_router.get("/{job_id}/log")
def job_log(job_id: str) -> FileResponse:
    manager = get_job_manager()
    job = manager.get(job_id)
    if not job.log_file:
        raise NotFoundError(
            ErrorCode.JOB_NOT_FOUND,
            "This render has no log file.",
            suggestion="Logs are written when a render reaches the export stage.",
        )
    repository = repo()
    paths = repository.paths_for(job.project_slug)
    target = safe_join(paths.exports, job.log_file)
    if not target.is_file():
        raise NotFoundError(
            ErrorCode.JOB_NOT_FOUND,
            f"The log file '{job.log_file}' is no longer on disk.",
        )
    return FileResponse(target, media_type="text/plain", filename=target.name)


@jobs_router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events carrying live progress for one job."""
    manager = get_job_manager()
    manager.get(job_id)  # 404 early rather than inside the stream

    async def stream():  # noqa: ANN202
        try:
            async for event in manager.subscribe(job_id):
                payload = json.dumps(event.model_dump(mode="json", by_alias=True))
                yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001 - never leave the client hanging
            logger.exception("SSE stream for job %s failed", job_id)
            error = json.dumps({"error": str(exc), "jobId": job_id})
            yield f"event: error\ndata: {error}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx-style proxies buffering the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )
