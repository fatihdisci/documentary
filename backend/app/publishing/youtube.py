"""The only module in the app that talks to YouTube.

Two responsibilities, kept apart:

* :class:`YouTubeCredentials` owns the desktop OAuth grant — finding the client
  file, reading and refreshing the token, running the browser flow, and reporting
  what state the connection is in.
* :class:`YouTubeClient` owns the API calls — resumable upload, thumbnail,
  captions, video status, channel.

Everything here is **blocking**. Callers on the event loop must go through
``anyio.to_thread.run_sync``; the job worker does exactly that.

Security rules this module enforces, not merely follows:

* A client id, client secret, access token or refresh token is never returned,
  logged, or put in an error payload. Google's own error bodies are filtered
  through :func:`_safe_error_detail` before they reach a log or the user.
* The token file is written 0600, atomically, into the app's secrets directory.
* "Disconnect" deletes the token and nothing else. The client file the user
  installed stays where it is.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, EnvironmentError_, NotFoundError, ValidationError
from app.publishing.models import YouTubeConnection
from app.storage.paths import sanitize_filename

logger = logging.getLogger("evb.publishing.youtube")

#: Everything the panel needs. ``force-ssl`` is what allows ``captions.insert``;
#: without it an SRT upload fails *after* the video is already on the channel,
#: which is exactly the failure mode this app refuses to have.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)

TOKEN_FILENAME = "youtube-upload-token.json"
#: Non-secret cache of the connected channel, so the Settings page does not have
#: to hit the network on every load.
CHANNEL_CACHE_FILENAME = "youtube-channel-cache.json"
CHANNEL_CACHE_MAX_AGE_SECONDS = 6 * 3600

#: Client files the app will consider. Google names its download
#: ``client_secret_<id>.apps.googleusercontent.com.json``.
CLIENT_FILE_GLOBS = ("client_secret_*.json", "oauth-client-*.json")

#: 8 MB chunks: large enough that the per-chunk overhead is irrelevant, small
#: enough that progress moves visibly and a cancel is honoured quickly.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

#: Anything that looks like a credential is scrubbed out of text that leaves
#: this module. Belt and braces on top of never putting one there deliberately.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(client_secret|client_id|refresh_token|access_token|id_token)"
               r"\"?\s*[:=]\s*\"?[^\s\",}]+"),
    re.compile(r"(?i)authorization:\s*\S+"),
    re.compile(r"ya29\.[A-Za-z0-9_\-]+"),
    re.compile(r"[0-9]{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"),
)


def _safe_error_detail(text: str, *, limit: int = 2000) -> str:
    """Strip anything credential-shaped out of a message before it is shown."""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[gizlendi]", cleaned)
    return cleaned[:limit]


# --- credentials ------------------------------------------------------------


@dataclass(frozen=True)
class _ClientFile:
    path: Path
    modified_at: float


class YouTubeCredentials:
    """Locates, validates, refreshes and stores the desktop OAuth grant."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- file locations ---------------------------------------------------

    @property
    def secrets_dir(self) -> Path:
        return self.settings.oauth_secrets_dir

    @property
    def token_file(self) -> Path:
        return self.secrets_dir / TOKEN_FILENAME

    def _client_candidates(self) -> list[_ClientFile]:
        directory = self.secrets_dir
        if not directory.is_dir():
            return []
        seen: dict[Path, _ClientFile] = {}
        for pattern in CLIENT_FILE_GLOBS:
            for path in directory.glob(pattern):
                if not path.is_file() or path in seen:
                    continue
                if not self._looks_like_client_file(path):
                    continue
                seen[path] = _ClientFile(path=path, modified_at=path.stat().st_mtime)
        return sorted(seen.values(), key=lambda entry: entry.modified_at, reverse=True)

    @staticmethod
    def _looks_like_client_file(path: Path) -> bool:
        """Cheap structural check, so a token file never masquerades as a client."""
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(raw, dict) and isinstance(raw.get("installed"), dict)

    def available_client_files(self) -> list[str]:
        """Basenames of every usable client file. Never a full path."""
        return [entry.path.name for entry in self._client_candidates()]

    def client_file(self) -> Path | None:
        """The client file to use: the configured one, else the newest valid one."""
        candidates = self._client_candidates()
        if not candidates:
            return None
        configured = (self.settings.mutable.youtube_client_secret_file or "").strip()
        if configured:
            chosen = next((c for c in candidates if c.path.name == configured), None)
            if chosen is not None:
                return chosen.path
            logger.info(
                "configured YouTube client file is missing; falling back to the newest one"
            )
        return candidates[0].path

    def require_client_file(self) -> Path:
        path = self.client_file()
        if path is None:
            raise EnvironmentError_(
                ErrorCode.YOUTUBE_CLIENT_MISSING,
                "YouTube için OAuth istemci dosyası bulunamadı.",
                details=f"aranan klasör: {self.secrets_dir}",
            )
        return path

    # --- installing a client file ----------------------------------------

    @staticmethod
    def validate_client_payload(data: bytes, original_name: str) -> dict[str, Any]:
        """Reject anything that is not a Desktop-app OAuth client file.

        Returns the parsed document so the caller can store it verbatim. The
        returned values are never logged or echoed back.
        """
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' geçerli bir JSON dosyası değil.",
                details=_safe_error_detail(str(exc)),
            ) from exc

        if not isinstance(raw, dict):
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' beklenen biçimde değil.",
                details="dosyanın en üst düzeyinde bir JSON nesnesi bekleniyor",
            )

        if "web" in raw and "installed" not in raw:
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' bir “Web application” istemcisi; bu uygulama “Desktop app” "
                "istemcisi kullanır.",
                details="dosyada 'installed' bölümü yok, 'web' bölümü var",
                suggestion=(
                    "Google Cloud Console'da yeni bir OAuth Client ID oluşturun ve tür olarak "
                    "“Desktop app” seçin."
                ),
            )

        installed = raw.get("installed")
        if not isinstance(installed, dict):
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' bir masaüstü OAuth istemci dosyası değil.",
                details="dosyada 'installed' bölümü bulunamadı",
            )

        missing = [
            field
            for field in ("client_id", "client_secret", "auth_uri", "token_uri")
            if not str(installed.get(field) or "").strip()
        ]
        if missing:
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' eksik: {', '.join(missing)} alanları yok.",
                details="Google'ın indirdiği dosyayı değiştirmeden yükleyin",
            )

        redirects = installed.get("redirect_uris")
        if redirects is not None and not isinstance(redirects, list):
            raise ValidationError(
                ErrorCode.YOUTUBE_CLIENT_INVALID,
                f"'{original_name}' içindeki yönlendirme adresleri okunamadı.",
                details="'redirect_uris' bir liste olmalı",
            )

        return raw

    def store_client_file(self, data: bytes, original_name: str) -> str:
        """Validate and install a client file. Returns its basename."""
        self.validate_client_payload(data, original_name)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.secrets_dir.chmod(0o700)
        except OSError:  # pragma: no cover - platform dependent
            pass

        name = sanitize_filename(original_name, default_stem="client-secret")
        if not name.endswith(".json"):
            name = f"{name}.json"
        if not name.startswith(("client_secret", "oauth-client-")):
            name = f"client_secret_{name}"

        target = self.secrets_dir / name
        tmp = target.with_suffix(".json.tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o600)
        tmp.replace(target)
        logger.info("installed a YouTube OAuth client file (%s)", name)
        return name

    # --- the grant --------------------------------------------------------

    def load_credentials(self, *, refresh: bool = True) -> Any | None:
        """Read the stored grant, refreshing it when it has expired.

        Returns ``None`` when there is no usable token file. Never raises for a
        missing token: "not connected" is a state, not an error.
        """
        from google.auth.exceptions import GoogleAuthError
        from google.oauth2.credentials import Credentials

        if not self.token_file.is_file():
            return None
        try:
            credentials = Credentials.from_authorized_user_file(str(self.token_file))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            logger.warning("stored YouTube token could not be read: %s", type(exc).__name__)
            return None

        if refresh and credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request

            try:
                credentials.refresh(Request())
            except (GoogleAuthError, OSError) as exc:
                # A revoked or invalidated grant lands here. The user has to
                # reconnect; nothing is deleted behind their back.
                logger.warning("YouTube token refresh failed: %s", type(exc).__name__)
                return credentials
            self._write_token(credentials)
        return credentials

    def _write_token(self, credentials: Any) -> None:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".json.tmp")
        tmp.write_text(credentials.to_json(), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.token_file)
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass

    def missing_scopes(self, credentials: Any | None) -> list[str]:
        granted = set(getattr(credentials, "scopes", None) or []) if credentials else set()
        return [scope for scope in SCOPES if scope not in granted]

    def usable_credentials(self) -> Any:
        """Credentials good enough to upload *and* to add captions, or an error.

        Every failure here is one the user can act on, and says which action.
        """
        credentials = self.load_credentials()
        if credentials is None:
            self.require_client_file()  # a missing client file is the better message
            raise AppError(
                ErrorCode.YOUTUBE_AUTH_REQUIRED,
                "YouTube hesabınız henüz bağlı değil.",
                details=f"kayıtlı yetki dosyası yok: {self.token_file.name}",
                http_status=401,
            )
        missing = self.missing_scopes(credentials)
        if missing:
            raise AppError(
                ErrorCode.YOUTUBE_SCOPE_MISSING,
                "YouTube bağlantınız altyazı yükleme yetkisini içermiyor.",
                details="eksik izinler:\n" + "\n".join(missing),
                http_status=403,
            )
        if not credentials.valid:
            raise AppError(
                ErrorCode.YOUTUBE_AUTH_REQUIRED,
                "YouTube bağlantınızın süresi dolmuş ve yenilenemedi.",
                details="kayıtlı yetki artık kullanılamıyor",
                http_status=401,
            )
        return credentials

    # --- connect / disconnect --------------------------------------------

    def connect(self) -> YouTubeConnection:
        """Run the desktop OAuth flow in the user's browser. Blocking."""
        from google.auth.exceptions import GoogleAuthError
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_file = self.require_client_file()
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_file), list(SCOPES))
            credentials = flow.run_local_server(host="localhost", port=0, open_browser=True)
        except (GoogleAuthError, ValueError, OSError, socket.error) as exc:
            raise AppError(
                ErrorCode.YOUTUBE_AUTH_FAILED,
                "YouTube bağlantısı tamamlanamadı.",
                details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
                http_status=502,
            ) from exc

        self._write_token(credentials)
        self._forget_channel_cache()
        logger.info("YouTube account connected")
        return self.status(refresh_channel=True)

    def disconnect(self) -> YouTubeConnection:
        """Delete the stored token. The installed client file is left alone."""
        self.token_file.unlink(missing_ok=True)
        self._forget_channel_cache()
        logger.info("YouTube token removed")
        return self.status(refresh_channel=False)

    # --- channel cache ----------------------------------------------------

    @property
    def _channel_cache_file(self) -> Path:
        return self.secrets_dir / CHANNEL_CACHE_FILENAME

    def _read_channel_cache(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._channel_cache_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _write_channel_cache(self, channel: dict[str, Any]) -> None:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        payload = {**channel, "cachedAt": datetime.now(timezone.utc).isoformat()}
        tmp = self._channel_cache_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self._channel_cache_file)

    def _forget_channel_cache(self) -> None:
        self._channel_cache_file.unlink(missing_ok=True)

    # --- status -----------------------------------------------------------

    def status(self, *, refresh_channel: bool = False) -> YouTubeConnection:
        """Describe the connection without ever revealing a credential."""
        available = self.available_client_files()
        client = self.client_file()
        credentials = self.load_credentials()

        connection = YouTubeConnection(
            client_file_present=client is not None,
            client_file_name=client.name if client else None,
            available_client_files=available,
            token_present=self.token_file.is_file(),
            checked_at=datetime.now(timezone.utc),
        )

        if client is None:
            connection.status_message = "OAuth istemci dosyası bulunamadı."
            connection.problem = (
                "Google Cloud'dan indirdiğiniz “Desktop app” OAuth istemci dosyası yüklü değil."
            )
            connection.suggestion = (
                "Aşağıdan dosyayı seçin ya da ~/ExtinctVideoBuilder/secrets/ klasörüne kopyalayın."
            )
            return connection

        if credentials is None:
            connection.status_message = "İstemci dosyası hazır, hesap henüz bağlanmadı."
            connection.suggestion = "“YouTube'a bağlan” düğmesine basın."
            return connection

        missing = self.missing_scopes(credentials)
        connection.scopes_sufficient = not missing
        connection.missing_scopes = missing
        connection.expired = bool(getattr(credentials, "expired", False))

        if missing:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantı var ama izinler yetersiz."
            connection.problem = (
                "Kayıtlı yetki altyazı yükleme iznini içermiyor."
            )
            connection.suggestion = (
                "Altyazı yükleme yetkisi için YouTube hesabınızı yeniden bağlayın."
            )
        elif not credentials.valid:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantının süresi dolmuş."
            connection.problem = "Kayıtlı yetki yenilenemedi; iptal edilmiş olabilir."
            connection.suggestion = "“Yeniden bağlan” düğmesine basın."
        else:
            connection.connected = True
            connection.status_message = "Bağlantı geçerli."

        channel = self._read_channel_cache()
        stale = True
        if channel is not None:
            cached_at = channel.get("cachedAt")
            try:
                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(str(cached_at))
                ).total_seconds()
                stale = age > CHANNEL_CACHE_MAX_AGE_SECONDS
            except (TypeError, ValueError):
                stale = True

        if connection.connected and (refresh_channel or channel is None or stale):
            try:
                channel = YouTubeClient(credentials).channel()
                self._write_channel_cache(channel)
            except AppError as exc:
                # A channel lookup failing does not make the connection invalid.
                logger.info("channel lookup failed: %s", exc.code.value)
                if channel is None:
                    connection.problem = exc.message
                    connection.suggestion = exc.suggestion

        if channel:
            connection.channel_id = str(channel.get("id") or "") or None
            connection.channel_title = str(channel.get("title") or "") or None
            thumbnail = channel.get("thumbnailUrl")
            connection.channel_thumbnail_url = str(thumbnail) if thumbnail else None

        return connection


# --- API client -------------------------------------------------------------


ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class UploadCancelled(Exception):
    """Raised inside the chunk loop when the user cancels a running upload."""


class YouTubeClient:
    """Thin, typed wrapper over the parts of the Data API this app uses."""

    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._service = build(
            "youtube", "v3", credentials=credentials, cache_discovery=False
        )

    # --- channel ----------------------------------------------------------

    def channel(self) -> dict[str, Any]:
        """The connected channel's id, title and avatar."""
        try:
            response = self._service.channels().list(part="snippet", mine=True).execute()
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise map_api_error(exc, stage="channel") from exc

        items = response.get("items") or []
        if not items:
            raise NotFoundError(
                ErrorCode.YOUTUBE_CHANNEL_NOT_FOUND,
                "Bu Google hesabına bağlı bir YouTube kanalı bulunamadı.",
                details="channels.list(mine=True) boş döndü",
            )
        snippet = items[0].get("snippet") or {}
        thumbnails = snippet.get("thumbnails") or {}
        best = thumbnails.get("medium") or thumbnails.get("default") or {}
        return {
            "id": items[0].get("id", ""),
            "title": snippet.get("title", ""),
            "thumbnailUrl": best.get("url"),
        }

    # --- upload -----------------------------------------------------------

    def upload_video(
        self,
        video: Path,
        *,
        body: dict[str, Any],
        notify_subscribers: bool,
        on_progress: ProgressCallback | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> dict[str, Any]:
        """Resumable upload. Returns the created video resource.

        Progress is reported per chunk, and cancellation is checked between
        chunks — the only point at which stopping leaves nothing half-created.
        """
        from googleapiclient.http import MediaFileUpload

        total = video.stat().st_size
        mime_type = mimetypes.guess_type(video.name)[0] or "video/mp4"
        media = MediaFileUpload(
            str(video),
            mimetype=mime_type,
            resumable=True,
            chunksize=UPLOAD_CHUNK_BYTES,
        )
        request = self._service.videos().insert(
            part="snippet,status",
            body=body,
            notifySubscribers=notify_subscribers,
            media_body=media,
        )

        response: dict[str, Any] | None = None
        while response is None:
            if is_cancelled is not None and is_cancelled():
                raise UploadCancelled()
            try:
                status, response = request.next_chunk()
            except Exception as exc:  # noqa: BLE001 - mapped below
                raise map_api_error(exc, stage="upload") from exc
            if status is not None and on_progress is not None:
                on_progress(int(status.resumable_progress), total)

        if on_progress is not None:
            on_progress(total, total)
        return response

    # --- extras -----------------------------------------------------------

    def set_thumbnail(self, video_id: str, thumbnail: Path) -> None:
        from googleapiclient.http import MediaFileUpload

        mime_type = mimetypes.guess_type(thumbnail.name)[0] or "image/jpeg"
        try:
            self._service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail), mimetype=mime_type),
            ).execute()
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise map_api_error(exc, stage="thumbnail") from exc

    def insert_caption(
        self,
        video_id: str,
        srt: Path,
        *,
        language: str = "en",
        name: str = "English",
        is_draft: bool = False,
    ) -> str:
        """Attach an SRT track. Returns the caption track id."""
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "videoId": video_id,
                "language": language,
                "name": name,
                "isDraft": is_draft,
            }
        }
        try:
            response = self._service.captions().insert(
                part="snippet",
                body=body,
                media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream"),
            ).execute()
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise map_api_error(exc, stage="caption") from exc
        return str(response.get("id", ""))

    def video_status(self, video_id: str) -> dict[str, Any]:
        """Processing/upload/privacy state for one video."""
        try:
            response = self._service.videos().list(
                part="status,processingDetails,snippet", id=video_id
            ).execute()
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise map_api_error(exc, stage="status") from exc

        items = response.get("items") or []
        if not items:
            return {}
        status = items[0].get("status") or {}
        processing = items[0].get("processingDetails") or {}
        return {
            "uploadStatus": status.get("uploadStatus"),
            "privacyStatus": status.get("privacyStatus"),
            "publishAt": status.get("publishAt"),
            "processingStatus": processing.get("processingStatus")
            or status.get("uploadStatus"),
            "title": (items[0].get("snippet") or {}).get("title"),
        }


# --- error mapping ----------------------------------------------------------


#: Reason strings Google uses for "you are out of quota".
_QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "uploadLimitExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}
_METADATA_REASONS = {
    "invalidTitle",
    "invalidDescription",
    "invalidTags",
    "invalidCategoryId",
    "invalidVideoMetadata",
    "invalidPublishAt",
    "invalidFilename",
    "defaultLanguageNotSupported",
}


def _reasons(payload: dict[str, Any]) -> set[str]:
    errors = ((payload.get("error") or {}).get("errors")) or []
    found = {str(item.get("reason", "")) for item in errors if isinstance(item, dict)}
    status = str((payload.get("error") or {}).get("status", ""))
    if status:
        found.add(status)
    return found


def map_api_error(exc: Exception, *, stage: str) -> AppError:
    """Turn a Google client exception into an error the user can act on.

    Quota, permission, invalid metadata, revoked grant and plain network trouble
    are all different problems with different fixes, so they get different codes.
    """
    from google.auth.exceptions import GoogleAuthError, RefreshError
    from googleapiclient.errors import HttpError

    stage_label = {
        "upload": "Video yüklenirken",
        "thumbnail": "Kapak görseli konulurken",
        "caption": "Altyazı eklenirken",
        "status": "Video durumu okunurken",
        "channel": "Kanal bilgisi okunurken",
    }.get(stage, "YouTube işlemi sırasında")

    failure_code = {
        "thumbnail": ErrorCode.YOUTUBE_THUMBNAIL_FAILED,
        "caption": ErrorCode.YOUTUBE_CAPTION_FAILED,
    }.get(stage, ErrorCode.YOUTUBE_UPLOAD_FAILED)

    if isinstance(exc, RefreshError):
        return AppError(
            ErrorCode.YOUTUBE_AUTH_REQUIRED,
            "YouTube yetkiniz artık geçerli değil.",
            details=_safe_error_detail(f"{type(exc).__name__}"),
            http_status=401,
        )

    if isinstance(exc, HttpError):
        status_code = int(getattr(exc.resp, "status", 0) or 0)
        try:
            payload = json.loads(exc.content.decode("utf-8"))
        except (AttributeError, ValueError, UnicodeDecodeError):
            payload = {}
        reasons = _reasons(payload if isinstance(payload, dict) else {})
        message = str((payload.get("error") or {}).get("message", "")) if payload else ""
        details = _safe_error_detail(
            f"HTTP {status_code}\n"
            + (", ".join(sorted(r for r in reasons if r)) or "sebep bildirilmedi")
            + (f"\n{message}" if message else "")
        )

        if reasons & _QUOTA_REASONS or status_code == 429:
            return AppError(
                ErrorCode.YOUTUBE_QUOTA_EXCEEDED,
                "YouTube kotası doldu, bu yüzden işlem tamamlanamadı.",
                details=details,
                http_status=429,
            )
        if status_code == 401:
            return AppError(
                ErrorCode.YOUTUBE_AUTH_REQUIRED,
                "YouTube yetkiniz kabul edilmedi.",
                details=details,
                http_status=401,
            )
        if status_code == 403 and (
            "insufficientPermissions" in reasons or "forbidden" in reasons
        ):
            return AppError(
                ErrorCode.YOUTUBE_SCOPE_MISSING,
                "Bu işlem için YouTube izinleriniz yetersiz.",
                details=details,
                http_status=403,
            )
        if reasons & _METADATA_REASONS:
            return ValidationError(
                ErrorCode.YOUTUBE_INVALID_METADATA,
                "YouTube gönderdiğimiz bilgileri kabul etmedi.",
                details=details,
            )
        return AppError(
            failure_code,
            f"{stage_label} YouTube bir hata döndürdü.",
            details=details,
            http_status=502,
        )

    if isinstance(exc, GoogleAuthError):
        return AppError(
            ErrorCode.YOUTUBE_AUTH_FAILED,
            "YouTube kimlik doğrulaması başarısız oldu.",
            details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
            http_status=502,
        )

    if isinstance(exc, (OSError, TimeoutError)):
        return AppError(
            ErrorCode.YOUTUBE_NETWORK_FAILED,
            f"{stage_label} YouTube'a ulaşılamadı.",
            details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
            http_status=504,
        )

    return AppError(
        failure_code,
        f"{stage_label} beklenmedik bir hata oluştu.",
        details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
        http_status=502,
    )
