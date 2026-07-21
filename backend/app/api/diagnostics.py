"""Diagnostics: everything the user needs to know whether the app can render.

Deliberately reports *measured* facts (probe the binary, stat the disk, write a
temp file) rather than assumptions.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from app.models.base import CamelModel

from app.config import get_settings
from app.errors import AppError
from app.render.ffmpeg import FFmpegRunner

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

CheckStatus = Literal["ok", "warn", "fail"]


class Check(CamelModel):
    id: str
    label: str
    status: CheckStatus
    value: str = ""
    detail: str = ""
    suggestion: str = ""


class DiagnosticsReport(CamelModel):
    generated_at: float
    healthy: bool
    checks: list[Check]
    notes: list[str] = []


def _tool_checks() -> tuple[list[Check], list[str]]:
    settings = get_settings()
    checks: list[Check] = []
    notes: list[str] = []
    try:
        caps = FFmpegRunner(settings).probe_capabilities()
    except AppError as exc:
        checks.append(
            Check(
                id="ffmpeg",
                label="FFmpeg",
                status="fail",
                value="not found",
                detail=exc.details or exc.message,
                suggestion=exc.suggestion,
            )
        )
        return checks, notes

    checks.append(
        Check(id="ffmpeg", label="FFmpeg", status="ok", value=caps.ffmpeg_version, detail=caps.ffmpeg_path)
    )
    checks.append(
        Check(id="ffprobe", label="ffprobe", status="ok", value=caps.ffprobe_version, detail=caps.ffprobe_path)
    )

    missing_filters = caps.missing_required_filters
    checks.append(
        Check(
            id="filters",
            label="Required FFmpeg filters",
            status="ok" if not missing_filters else "fail",
            value="all present" if not missing_filters else f"missing: {', '.join(missing_filters)}",
            detail=f"{len(caps.filters)} filters detected",
            suggestion="" if not missing_filters else "Install a complete FFmpeg build (brew install ffmpeg).",
        )
    )

    missing_encoders = caps.missing_required_encoders
    checks.append(
        Check(
            id="encoders",
            label="Required encoders",
            status="ok" if not missing_encoders else "fail",
            value="libx264 + aac present" if not missing_encoders else f"missing: {', '.join(missing_encoders)}",
            detail=f"{len(caps.encoders)} encoders detected",
            suggestion="" if not missing_encoders else "Install an FFmpeg build with libx264 and AAC support.",
        )
    )

    # The headline finding for this machine: no drawtext. Reported as OK, not a
    # warning, because the Pillow path is the app's intended design.
    checks.append(
        Check(
            id="text-engine",
            label="Text rendering engine",
            status="ok",
            value="Pillow (bundled fonts)",
            detail=(
                "drawtext: " + ("available" if caps.has_drawtext else "NOT available") + " · "
                "libass: " + ("available" if caps.has_libass else "NOT available") + ". "
                "This app renders all text with Pillow either way, so output is identical "
                "across machines regardless of how FFmpeg was compiled."
            ),
        )
    )

    checks.append(
        Check(
            id="transitions",
            label="Transitions (xfade)",
            status="ok" if caps.has_xfade else "warn",
            value="available" if caps.has_xfade else "unavailable — hard cuts only",
            suggestion="" if caps.has_xfade else "Install a fuller FFmpeg build to enable dissolves.",
        )
    )
    checks.append(
        Check(
            id="ducking",
            label="Music ducking (sidechaincompress)",
            status="ok" if caps.has_sidechain else "warn",
            value="available" if caps.has_sidechain else "unavailable — static music level",
        )
    )
    checks.append(
        Check(
            id="loudness",
            label="Loudness normalization (loudnorm)",
            status="ok" if caps.has_loudnorm else "warn",
            value="available" if caps.has_loudnorm else "unavailable — fixed gain",
        )
    )
    checks.append(
        Check(
            id="hwaccel",
            label="Hardware encoder",
            status="ok",
            value="h264_videotoolbox available" if caps.has_videotoolbox else "software only",
            detail="Software libx264 is always the default; hardware encoding is opt-in.",
        )
    )
    checks.append(
        Check(
            id="prores",
            label="ProRes intermediate",
            status="ok",
            value="available" if caps.has_prores else "unavailable",
            detail="Used only as an optional intermediate codec for cached scene clips.",
        )
    )
    notes.extend(caps.notes())
    return checks, notes


def _writable_check(check_id: str, label: str, directory: Path) -> Check:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-probe-{os.getpid()}"
        probe.write_text("ok", "utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            id=check_id,
            label=label,
            status="fail",
            value="not writable",
            detail=f"{directory}: {exc}",
            suggestion="Check permissions, or choose a different directory in Settings.",
        )
    return Check(id=check_id, label=label, status="ok", value=str(directory))


def _disk_check(directory: Path) -> Check:
    try:
        usage = shutil.disk_usage(directory if directory.exists() else directory.parent)
    except OSError as exc:
        return Check(id="disk", label="Free disk space", status="warn", value="unknown", detail=str(exc))
    free_gb = usage.free / 1024**3
    total_gb = usage.total / 1024**3
    # A 7-minute 1080p60 render with cached intermediates peaks in the low GBs.
    status: CheckStatus = "ok" if free_gb >= 10 else "warn" if free_gb >= 3 else "fail"
    return Check(
        id="disk",
        label="Free disk space",
        status=status,
        value=f"{free_gb:.1f} GB free of {total_gb:.0f} GB",
        suggestion=(
            "" if status == "ok"
            else "Free up space before rendering; intermediates for a 7-minute video need a few GB."
        ),
    )


def _tts_check() -> Check:
    """Report which narration sources actually work right now.

    'imported' (upload your own audio) is always available, so a failure here
    never means the app cannot produce a video — only that online TTS is down.
    """
    from app.tts.registry import provider_status_summary

    summary = provider_status_summary()
    available = [name for name, status in summary.items() if status.available]
    online = [name for name in available if not summary[name].offline]

    return Check(
        id="tts",
        label="Narration sources",
        status="ok" if online else "warn",
        value=", ".join(available) if available else "none",
        detail=" · ".join(f"{name}: {status.message}" for name, status in sorted(summary.items())),
        suggestion=(
            "" if online
            else "No online TTS provider is configured or reachable. You can still upload "
                 "narration audio per scene and render the project completely offline."
        ),
    )


@router.get("", response_model=DiagnosticsReport)
def get_diagnostics() -> DiagnosticsReport:
    settings = get_settings()
    checks: list[Check] = [
        Check(
            id="backend",
            label="Backend",
            status="ok",
            value=f"running · Python {sys.version.split()[0]}",
            detail=f"pid {os.getpid()}",
        )
    ]
    tool_checks, notes = _tool_checks()
    checks.extend(tool_checks)
    checks.append(_writable_check("storage", "Project storage", settings.projects_dir))
    checks.append(_writable_check("exports", "Export directory", settings.exports_dir))
    checks.append(_writable_check("temp", "Temporary directory", settings.temp_dir))
    checks.append(_disk_check(settings.data_dir))
    checks.append(_tts_check())

    return DiagnosticsReport(
        generated_at=time.time(),
        healthy=all(c.status != "fail" for c in checks),
        checks=checks,
        notes=notes,
    )
