"""Settings and secrets endpoints.

Secret *values* are write-only: they can be set and cleared, and their presence
can be queried, but they are never returned by any endpoint or written to a log.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import Field

from app.models.base import CamelModel

from app.config import MutableSettings, get_settings
from app.errors import AppError, ErrorCode, ValidationError

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Secrets the app knows about. Anything else is rejected so a typo cannot
#: silently create a dead key.
KNOWN_SECRETS = {"elevenlabs_api_key"}


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
        configured_secrets=[k for k in settings.secret_names() if k in KNOWN_SECRETS],
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
                f"The configured {tool} path does not exist: {candidate}",
                details=f"{tool}_path={configured!r}",
                suggestion=f"Leave it as '{tool}' to search PATH, or point it at a real executable.",
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
                f"Cannot use {directory} as the {field_name.replace('_', ' ')}.",
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
            f"Unknown secret '{update.key}'.",
            details=f"known secrets: {', '.join(sorted(KNOWN_SECRETS))}",
            suggestion="Check the spelling of the secret name.",
        )
    try:
        get_settings().set_secret(update.key, update.value or None)
    except OSError as exc:
        raise AppError(
            ErrorCode.PERMISSION_DENIED,
            "Could not write the secrets file.",
            details=str(exc),
        ) from exc
    return _build_response()
