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
                value="bulunamadı",
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
            label="Gerekli FFmpeg özellikleri",
            status="ok" if not missing_filters else "fail",
            value="hepsi var" if not missing_filters else f"eksik: {', '.join(missing_filters)}",
            detail=f"{len(caps.filters)} özellik bulundu",
            suggestion="" if not missing_filters else "Tam sürüm FFmpeg kurun (brew install ffmpeg).",
        )
    )

    missing_encoders = caps.missing_required_encoders
    checks.append(
        Check(
            id="encoders",
            label="Gerekli kodlayıcılar",
            status="ok" if not missing_encoders else "fail",
            value="libx264 + aac var" if not missing_encoders else f"eksik: {', '.join(missing_encoders)}",
            detail=f"{len(caps.encoders)} kodlayıcı bulundu",
            suggestion="" if not missing_encoders else "libx264 ve AAC destekleyen bir FFmpeg kurun.",
        )
    )

    # The headline finding for this machine: no drawtext. Reported as OK, not a
    # warning, because the Pillow path is the app's intended design.
    checks.append(
        Check(
            id="text-engine",
            label="Yazı çizimi",
            status="ok",
            value="Pillow (uygulamayla gelen yazı tipleri)",
            detail=(
                "drawtext: " + ("var" if caps.has_drawtext else "YOK") + " · "
                "libass: " + ("var" if caps.has_libass else "YOK") + ". "
                "Bu uygulama yazıları her hâlükârda Pillow ile çizer; bu yüzden sonuç her "
                "bilgisayarda birebir aynı görünür."
            ),
        )
    )

    checks.append(
        Check(
            id="transitions",
            label="Sahne geçişleri",
            status="ok" if caps.has_xfade else "warn",
            value="kullanılabilir" if caps.has_xfade else "yok — sahneler sert kesmeyle birleşir",
            suggestion="" if caps.has_xfade else "Yumuşak geçişler için tam sürüm FFmpeg kurun.",
        )
    )
    checks.append(
        Check(
            id="ducking",
            label="Konuşurken müziği kısma",
            status="ok" if caps.has_sidechain else "warn",
            value="kullanılabilir" if caps.has_sidechain else "yok — müzik hep aynı seviyede kalır",
        )
    )
    checks.append(
        Check(
            id="loudness",
            label="Ses seviyesi dengeleme",
            status="ok" if caps.has_loudnorm else "warn",
            value="kullanılabilir" if caps.has_loudnorm else "yok — sabit ses seviyesi",
        )
    )
    checks.append(
        Check(
            id="hwaccel",
            label="Ekran kartıyla hızlandırma",
            status="ok",
            value="kullanılabilir" if caps.has_videotoolbox else "yok — işlemci kullanılacak",
            detail="Varsayılan her zaman işlemcidir; ekran kartı isteğe bağlıdır.",
        )
    )
    checks.append(
        Check(
            id="prores",
            label="ProRes ara biçimi",
            status="ok",
            value="kullanılabilir" if caps.has_prores else "yok",
            detail="Yalnızca sahnelerin geçici dosyaları için isteğe bağlı bir seçenektir.",
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
            value="yazılamıyor",
            detail=f"{directory}: {exc}",
            suggestion="Klasör izinlerini kontrol edin ya da Ayarlar'dan başka bir klasör seçin.",
        )
    return Check(id=check_id, label=label, status="ok", value=str(directory))


def _disk_check(directory: Path) -> Check:
    try:
        usage = shutil.disk_usage(directory if directory.exists() else directory.parent)
    except OSError as exc:
        return Check(id="disk", label="Boş disk alanı", status="warn", value="bilinmiyor", detail=str(exc))
    free_gb = usage.free / 1024**3
    total_gb = usage.total / 1024**3
    # A 7-minute 1080p60 render with cached intermediates peaks in the low GBs.
    status: CheckStatus = "ok" if free_gb >= 10 else "warn" if free_gb >= 3 else "fail"
    return Check(
        id="disk",
        label="Boş disk alanı",
        status=status,
        value=f"{total_gb:.0f} GB'ın {free_gb:.1f} GB'ı boş",
        suggestion=(
            "" if status == "ok"
            else "Video oluşturmadan önce yer açın; 7 dakikalık bir video birkaç GB yer ister."
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
        label="Seslendirme kaynakları",
        status="ok" if online else "warn",
        value=", ".join(available) if available else "yok",
        detail=" · ".join(f"{name}: {status.message}" for name, status in sorted(summary.items())),
        suggestion=(
            "" if online
            else "Şu anda çevrimiçi bir seslendirme servisine ulaşılamıyor. Her sahne için "
                 "kendi ses kaydınızı yükleyip videoyu internetsiz de oluşturabilirsiniz."
        ),
    )


@router.get("", response_model=DiagnosticsReport)
def get_diagnostics() -> DiagnosticsReport:
    settings = get_settings()
    checks: list[Check] = [
        Check(
            id="backend",
            label="Uygulama",
            status="ok",
            value=f"çalışıyor · Python {sys.version.split()[0]}",
            detail=f"pid {os.getpid()}",
        )
    ]
    tool_checks, notes = _tool_checks()
    checks.extend(tool_checks)
    checks.append(_writable_check("storage", "Projeler klasörü", settings.projects_dir))
    checks.append(_writable_check("exports", "Videolar klasörü", settings.exports_dir))
    checks.append(_writable_check("temp", "Geçici dosyalar klasörü", settings.temp_dir))
    checks.append(_disk_check(settings.data_dir))
    checks.append(_tts_check())

    return DiagnosticsReport(
        generated_at=time.time(),
        healthy=all(c.status != "fail" for c in checks),
        checks=checks,
        notes=notes,
    )
