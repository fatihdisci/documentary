"""FastAPI application entry point.

Run in development with::

    backend/.venv/bin/uvicorn app.main:app --reload --port 8756

or use ``./dev.sh`` from the repo root to start the backend and frontend together.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import audio, diagnostics, projects, render, settings_api, shorts
from app.config import configure_logging, get_settings
from app.errors import AppError, ErrorCode, ErrorPayload

logger = logging.getLogger("evb.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    log_file = configure_logging(settings)
    logger.info("Extinct Video Builder backend starting")
    logger.info("data dir: %s", settings.data_dir)
    logger.info("log file: %s", log_file)

    # Report the FFmpeg situation once at startup so it is in the log before any
    # render is attempted, rather than only when one fails.
    try:
        from app.render.ffmpeg import FFmpegRunner

        caps = FFmpegRunner(settings).probe_capabilities()
        logger.info("%s", caps.ffmpeg_version)
        for note in caps.notes():
            logger.info("ffmpeg note: %s", note)
        if not caps.is_usable:
            logger.error(
                "FFmpeg is missing required capabilities: filters=%s encoders=%s",
                caps.missing_required_filters,
                caps.missing_required_encoders,
            )
    except AppError as exc:
        logger.error("FFmpeg unavailable at startup: %s", exc)

    # Loads render history and marks any render that was killed mid-run.
    from app.render.jobs import get_job_manager
    from app.shorts.jobs import get_short_job_manager

    manager = get_job_manager()
    await manager.start()
    # Same treatment for Shorts: its own queue and history, marking any Short
    # that was being built when the app stopped.
    short_manager = get_short_job_manager()
    await short_manager.start()

    yield

    await short_manager.stop()
    await manager.stop()
    logger.info("backend shutting down")


app = FastAPI(
    title="Extinct Video Builder",
    version="1.0.0",
    description="Local-first documentary video builder for extinct-animal videos.",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.dev_origin, "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Every deliberate failure becomes a structured, actionable payload."""
    logger.warning("%s %s -> %s: %s", request.method, request.url.path, exc.code.value, exc.message)
    payload = exc.to_payload()
    if payload.log_path is None:
        payload.log_path = str(get_settings().logs_dir / "backend.log")
    return JSONResponse(status_code=exc.http_status, content=payload.model_dump(mode="json"))


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Unexpected errors still get a real message and the traceback, never a shrug."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    payload = ErrorPayload(
        code=ErrorCode.INTERNAL,
        message=f"An unexpected {type(exc).__name__} occurred while handling {request.url.path}.",
        details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
        suggestion="This is a bug. The traceback above and the backend log have the details.",
        log_path=str(get_settings().logs_dir / "backend.log"),
    )
    return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "extinct-video-builder"}


app.include_router(diagnostics.router)
app.include_router(settings_api.router)
app.include_router(projects.router)
app.include_router(audio.router)
app.include_router(audio.providers_router)
app.include_router(render.router)
app.include_router(render.jobs_router)
app.include_router(shorts.router)
app.include_router(shorts.jobs_router)


def mount_frontend() -> None:
    """Serve the built frontend in production mode, if it has been built.

    In development the Vite dev server handles this and the mount is skipped.
    """
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
        logger.info("serving built frontend from %s", dist)


mount_frontend()
