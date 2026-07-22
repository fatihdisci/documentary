"""Application settings and on-disk layout.

Settings come from three layers, later ones winning: built-in defaults, the
``.env`` file / environment, then ``settings.json`` in the data directory
(written by the Settings page).

API keys live in ``secrets.json`` with 0600 permissions and are never included
in settings responses, logs, or project bundles.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import EnvironmentError_, ErrorCode
from app.models.enums import IntermediateCodec, QualityPreset, TTSProviderName, TransitionPreset

logger = logging.getLogger("evb.config")

DEFAULT_DATA_DIR = Path.home() / "ExtinctVideoBuilder"

#: Filenames that must never be served or bundled.
SECRETS_FILENAME = "secrets.json"
SETTINGS_FILENAME = "settings.json"


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.capitalize() for w in rest)


class MutableSettings(BaseModel):
    """The subset of settings the user can change from the Settings page.

    Deliberately excludes secrets — those go through a separate endpoint that
    only ever accepts values and reports presence, never reads them back.
    """

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    projects_dir: str = ""
    exports_dir: str = ""
    temp_dir: str = ""

    tts_provider: TTSProviderName = TTSProviderName.EDGE
    default_voice: str = "en-US-AndrewNeural"
    default_font: str = "Inter"
    default_fps: int = 60
    default_width: int = 1920
    default_height: int = 1080
    default_transition: TransitionPreset = TransitionPreset.DOCUMENTARY_DISSOLVE
    default_scene_lead_in_seconds: float = 0.35
    default_scene_tail_seconds: float = 0.65
    default_quality: QualityPreset = QualityPreset.YOUTUBE_HQ
    intermediate_codec: IntermediateCodec = IntermediateCodec.H264_CRF14_FAST

    use_hardware_encoder: bool = False
    #: Delete a render's temp folder when it completes successfully.
    cleanup_temp_on_success: bool = True
    #: Keep temp folders of failed renders for this many days, then reap them.
    temp_retention_days: int = 3
    log_level: str = "INFO"

    max_upload_mb: int = 64
    max_json_mb: int = 8
    #: Fail a render preflight unless this much headroom remains afterwards.
    disk_safety_margin_mb: int = 1024


class Settings(BaseSettings):
    """Process-level settings; the data directory is fixed at startup."""

    model_config = SettingsConfigDict(env_prefix="EVB_", env_file=".env", extra="ignore")

    data_dir: Path = DEFAULT_DATA_DIR
    host: str = "127.0.0.1"
    port: int = 8756
    #: Vite dev server origin, allowed through CORS in development.
    dev_origin: str = "http://localhost:5173"

    _mutable_cache: MutableSettings | None = PrivateAttr(default=None)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.data_dir = Path(self.data_dir).expanduser()

    # --- Directory layout -------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        return self._override_dir("projects_dir", self.data_dir / "projects")

    @property
    def exports_dir(self) -> Path:
        return self._override_dir("exports_dir", self.data_dir / "exports")

    @property
    def temp_dir(self) -> Path:
        return self._override_dir("temp_dir", self.data_dir / "temp")

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def music_library_dir(self) -> Path:
        return self.data_dir / "music"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / SETTINGS_FILENAME

    @property
    def secrets_file(self) -> Path:
        return self.data_dir / SECRETS_FILENAME

    def _override_dir(self, field: str, default: Path) -> Path:
        configured = getattr(self.mutable, field, "")
        return Path(configured).expanduser() if configured else default

    def all_dirs(self) -> list[Path]:
        return [
            self.data_dir,
            self.projects_dir,
            self.exports_dir,
            self.temp_dir,
            self.logs_dir,
            self.cache_dir,
            self.music_library_dir,
        ]

    def ensure_dirs(self) -> None:
        for directory in self.all_dirs():
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
                raise EnvironmentError_(
                    ErrorCode.PERMISSION_DENIED,
                    f"Cannot create the application directory {directory}.",
                    details=str(exc),
                ) from exc

    # --- Mutable settings persistence ------------------------------------

    @property
    def mutable(self) -> MutableSettings:
        if self._mutable_cache is None:
            self._mutable_cache = self.load_mutable()
        return self._mutable_cache

    def load_mutable(self) -> MutableSettings:
        if not self.settings_file.exists():
            return MutableSettings()
        try:
            raw = json.loads(self.settings_file.read_text("utf-8"))
            return MutableSettings.model_validate(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            # A corrupt settings file must not brick the app; fall back loudly.
            logger.warning("settings.json is invalid, falling back to defaults: %s", exc)
            return MutableSettings()

    def save_mutable(self, value: MutableSettings) -> MutableSettings:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(value.model_dump(by_alias=True, mode="json"), indent=2), "utf-8")
        tmp.replace(self.settings_file)
        self._mutable_cache = value
        return value

    def reload_mutable(self) -> MutableSettings:
        self._mutable_cache = None
        return self.mutable

    # --- Secrets ----------------------------------------------------------

    def _read_secrets(self) -> dict[str, str]:
        if not self.secrets_file.exists():
            return {}
        try:
            data = json.loads(self.secrets_file.read_text("utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            logger.warning("secrets.json is invalid and was ignored")
            return {}

    def get_secret(self, key: str) -> str | None:
        # Environment wins so CI and one-off runs never need to write the file.
        env_value = os.environ.get(f"EVB_SECRET_{key.upper()}")
        if env_value:
            return env_value
        return self._read_secrets().get(key) or None

    def set_secret(self, key: str, value: str | None) -> None:
        secrets = self._read_secrets()
        if value:
            secrets[key] = value
        else:
            secrets.pop(key, None)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.secrets_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(secrets, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.secrets_file)

    def secret_names(self) -> list[str]:
        """Names only — values never leave this class."""
        return sorted(self._read_secrets().keys())

    # --- Tool resolution --------------------------------------------------

    def resolve_tool(self, name: str) -> str | None:
        """Resolve ``ffmpeg``/``ffprobe`` to an executable path, or None."""
        configured = getattr(self.mutable, f"{name}_path", name) or name
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
        found = shutil.which(configured)
        if found:
            return found
        # Homebrew on Apple Silicon is not always on a GUI app's PATH.
        for fallback in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
            if Path(fallback).is_file() and os.access(fallback, os.X_OK):
                return fallback
        return None

    def require_tool(self, name: str) -> str:
        path = self.resolve_tool(name)
        if path is None:
            code = ErrorCode.FFMPEG_NOT_FOUND if name == "ffmpeg" else ErrorCode.FFPROBE_NOT_FOUND
            raise EnvironmentError_(
                code,
                f"{name} could not be found.",
                details=(
                    f"Configured value: {getattr(self.mutable, f'{name}_path', name)!r}\n"
                    f"PATH: {os.environ.get('PATH', '')}"
                ),
            )
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def configure_logging(settings: Settings) -> Path:
    """Attach a rotating-ish file handler plus console output. Returns log path."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.logs_dir / "backend.log"
    level = getattr(logging, settings.mutable.log_level.upper(), logging.INFO)

    root = logging.getLogger("evb")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.propagate = False
    return log_file
