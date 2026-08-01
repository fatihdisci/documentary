"""Settings and secrets endpoints.

Secret *values* are write-only: they can be set and cleared, and their presence
can be queried, but they are never returned by any endpoint or written to a log.

The one exception is deliberate and sealed: ``/export`` produces an
**encrypted** bundle for moving an installation to another computer. It is the
only route that reads secret values, the passphrase is required, and what comes
back is ciphertext — see ``app/config_bundle.py``.
"""

from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import Field

from app.models.base import CamelModel

from app.config import MutableSettings, get_settings
from app.config_bundle import (
    MAX_BUNDLE_BYTES,
    BundleContents,
    BundleExportRequest,
    BundleImportResult,
    export_bundle,
    import_bundle,
    read_bundle_header,
)
from app.errors import AppError, ErrorCode, ValidationError

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Secrets the app knows about. Anything else is rejected so a typo cannot
#: silently create a dead key.
#:
#: The publishing credentials are listed so their *presence* can be reported,
#: but they are deliberately not settable through this endpoint: each one has
#: its own route that validates the value's shape and refuses to overwrite a
#: working pair by accident.
KNOWN_SECRETS = {"elevenlabs_api_key"}

READ_ONLY_SECRETS = {
    "tiktok_client_key",
    "tiktok_client_secret",
}


class SettingsResponse(CamelModel):
    settings: MutableSettings
    #: Names of configured secrets. Never their values.
    configured_secrets: list[str] = Field(default_factory=list)
    resolved_paths: dict[str, str] = Field(default_factory=dict)


class SecretUpdate(CamelModel):
    key: str
    #: None or empty clears the secret.
    value: str | None = None


def _build_response() -> SettingsResponse:
    settings = get_settings()
    return SettingsResponse(
        settings=settings.mutable,
        configured_secrets=[
            name
            for name in settings.secret_names()
            if name in KNOWN_SECRETS or name in READ_ONLY_SECRETS
        ],
        resolved_paths={
            "dataDir": str(settings.data_dir),
            "projectsDir": str(settings.projects_dir),
            "exportsDir": str(settings.exports_dir),
            "tempDir": str(settings.temp_dir),
            "logsDir": str(settings.logs_dir),
            "musicLibraryDir": str(settings.music_library_dir),
            "ffmpeg": settings.resolve_tool("ffmpeg") or "",
            "ffprobe": settings.resolve_tool("ffprobe") or "",
        },
    )


@router.get("", response_model=SettingsResponse)
def read_settings() -> SettingsResponse:
    return _build_response()


@router.put("", response_model=SettingsResponse)
def update_settings(value: MutableSettings) -> SettingsResponse:
    settings = get_settings()

    # Validate executable paths before persisting, so a typo cannot leave the
    # app in a state where nothing renders and the Settings page cannot be used.
    for tool in ("ffmpeg", "ffprobe"):
        configured = getattr(value, f"{tool}_path", "") or tool
        candidate = Path(configured).expanduser()
        if candidate.is_absolute() and not candidate.is_file():
            raise ValidationError(
                ErrorCode.FFMPEG_NOT_FOUND if tool == "ffmpeg" else ErrorCode.FFPROBE_NOT_FOUND,
                f"Belirtilen {tool} konumu yok: {candidate}",
                details=f"{tool}_path={configured!r}",
                suggestion=f"Uygulamanın kendisi arasın diye '{tool}' yazın ya da gerçek bir dosya yolu verin.",
            )

    # Directory overrides must be creatable, otherwise projects silently vanish.
    for field_name in ("projects_dir", "exports_dir", "temp_dir"):
        configured = getattr(value, field_name, "")
        if not configured:
            continue
        directory = Path(configured).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(
                ErrorCode.PERMISSION_DENIED,
                f"{directory} klasörü kullanılamıyor.",
                details=str(exc),
            ) from exc

    settings.save_mutable(value)
    settings.ensure_dirs()
    return _build_response()


@router.post("/secrets", response_model=SettingsResponse)
def set_secret(update: SecretUpdate) -> SettingsResponse:
    if update.key not in KNOWN_SECRETS:
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            f"Tanınmayan anahtar: '{update.key}'.",
            details=f"known secrets: {', '.join(sorted(KNOWN_SECRETS))}",
            suggestion="Anahtar adının yazımını kontrol edin.",
        )
    try:
        get_settings().set_secret(update.key, update.value or None)
    except OSError as exc:
        raise AppError(
            ErrorCode.PERMISSION_DENIED,
            "Anahtar dosyası yazılamadı.",
            details=str(exc),
        ) from exc
    return _build_response()


# --- moving an installation to another computer -----------------------------


@router.post("/export")
async def export_settings(request: BundleExportRequest) -> Response:
    """Pack settings, keys and OAuth grants into one passphrase-sealed file.

    Returns the file itself rather than JSON: the browser saves it, and the
    bytes never sit in a JavaScript variable that some other part of the page
    could read. Key derivation is deliberately slow, so it runs off the loop.
    """
    data, filename, contents = await anyio.to_thread.run_sync(export_bundle, request)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Counts only, so the page can confirm what it just downloaded
            # without opening it. No name of any key appears here.
            "X-Evb-Bundle-Secrets": str(contents.secrets),
            "X-Evb-Bundle-Credential-Files": str(contents.credential_files),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Evb-Bundle-Secrets, X-Evb-Bundle-Credential-Files"
            ),
        },
    )


@router.post("/import/inspect", response_model=BundleContents)
async def inspect_settings_bundle(file: UploadFile = File(...)) -> BundleContents:
    """What a bundle holds, without the passphrase.

    So picking the wrong file is answered immediately, instead of arriving as a
    confusing "wrong passphrase" after the user has typed one.
    """
    data = await _read_bundle(file)
    return await anyio.to_thread.run_sync(read_bundle_header, data)


@router.post("/import", response_model=BundleImportResult)
async def import_settings_bundle(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
    overwrite: bool = Form(default=True),
    include_paths: bool = Form(default=False),
) -> BundleImportResult:
    """Restore a bundle. Reports what it did **by name**, never by value."""
    data = await _read_bundle(file)
    return await anyio.to_thread.run_sync(
        lambda: import_bundle(
            data, passphrase, overwrite=overwrite, include_paths=include_paths
        )
    )


async def _read_bundle(file: UploadFile) -> bytes:
    data = await file.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Bu dosya bir ayar paketi için fazla büyük.",
            details=f"limit: {MAX_BUNDLE_BYTES} bayt",
        )
    return data
