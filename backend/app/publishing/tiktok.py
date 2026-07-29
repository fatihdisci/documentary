"""The only module in the app that talks to TikTok.

TikTok is the platform with the most honest constraint in it, and this module is
built around saying so rather than hiding it: until a TikTok app passes the
**Content Posting API audit**, everything it posts is restricted to the creator's
own view. The app does not pretend otherwise, does not offer a "public" option it
knows will be refused, and does not report a post as public when it is not.

Two responsibilities, kept apart:

* :class:`TikTokCredentials` owns the grant — the client key and secret, the
  authorization URL with its PKCE challenge, the code exchange, the refresh, and
  the connection report.
* :class:`TikTokClient` owns the API calls — the creator-info query that says
  what this account may currently do, the Direct Post init, the chunked upload,
  and the publish-status poll.

Unlike Meta, TikTok takes the **file itself**: the bytes go straight from this
computer to TikTok's upload URL, so the temporary hosting layer is not involved
and the video is never parked anywhere.

Everything here is blocking and runs in a worker thread. No client secret,
refresh token or access token is ever returned, logged, or put in an error.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, ValidationError
from app.publishing.models import (
    OAuthStart,
    TikTokConnection,
    TikTokCreatorInfo,
)

logger = logging.getLogger("evb.publishing.tiktok")

#: ``video.publish`` is the Direct Post permission — the one that makes a video
#: appear on the account rather than merely landing in the drafts inbox.
SCOPES: tuple[str, ...] = ("user.info.basic", "video.publish")

CLIENT_KEY_SECRET = "tiktok_client_key"
CLIENT_SECRET_SECRET = "tiktok_client_secret"

TOKEN_FILENAME = "tiktok-token.json"

AUTH_HOST = "https://www.tiktok.com"
API_HOST = "https://open.tiktokapis.com"

STATE_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 60.0
UPLOAD_TIMEOUT_SECONDS = 900.0

#: TikTok's own chunking rules: at least 5 MB per chunk, at most 64 MB, and a
#: single-chunk upload for anything that fits.
MIN_CHUNK_BYTES = 5 * 1024 * 1024
MAX_SINGLE_CHUNK_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 10 * 1024 * 1024

#: How long the job waits for TikTok to finish processing a post.
PUBLISH_TIMEOUT_SECONDS = 10 * 60
PUBLISH_POLL_SECONDS = 5.0

#: The only privacy an unaudited app may use.
SELF_ONLY = "SELF_ONLY"

_SECRET_PATTERNS = (
    re.compile(r"(?i)(access_token|refresh_token|client_secret|client_key|code_verifier)"
               r"\"?\s*[:=]\s*\"?[^\s\",&}]+"),
    re.compile(r"(?i)authorization:\s*\S+"),
    re.compile(r"act\.[A-Za-z0-9!._-]{20,}"),
)


def _safe_error_detail(text: str, *, limit: int = 2000) -> str:
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[gizlendi]", cleaned)
    return cleaned[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _PendingAuth:
    verifier: str
    started_at: float


# --- credentials ------------------------------------------------------------


class TikTokCredentials:
    """Stores the app credentials and the grant, and reports the connection."""

    _pending: dict[str, _PendingAuth] = {}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- app credentials --------------------------------------------------

    @property
    def client_key(self) -> str:
        return (self.settings.get_secret(CLIENT_KEY_SECRET) or "").strip()

    @property
    def _client_secret(self) -> str:
        return (self.settings.get_secret(CLIENT_SECRET_SECRET) or "").strip()

    @property
    def app_configured(self) -> bool:
        return bool(self.client_key and self._client_secret)

    def store_app_credentials(
        self, client_key: str, client_secret: str, *, replace: bool
    ) -> None:
        client_key = (client_key or "").strip()
        client_secret = (client_secret or "").strip()

        if self.app_configured and not replace:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok uygulama bilgileri zaten kayıtlı.",
                details="mevcut kayıt korunmak için değiştirilmedi",
                suggestion=(
                    "Değiştirmek istiyorsanız “Kimlik bilgilerini değiştir” seçeneğini "
                    "işaretleyin."
                ),
                http_status=409,
            )
        if len(client_key) < 8 or not re.fullmatch(r"[A-Za-z0-9._-]+", client_key):
            raise ValidationError(
                ErrorCode.TIKTOK_APP_MISSING,
                "TikTok Client Key beklenen biçimde değil.",
                details=f"girilen değerin uzunluğu: {len(client_key)}",
            )
        if len(client_secret) < 16 or not re.fullmatch(r"[A-Za-z0-9._-]+", client_secret):
            raise ValidationError(
                ErrorCode.TIKTOK_APP_MISSING,
                "TikTok Client Secret beklenen biçimde değil.",
                details="Client Secret uzun bir harf-rakam dizesidir",
            )

        self.settings.set_secret(CLIENT_KEY_SECRET, client_key)
        self.settings.set_secret(CLIENT_SECRET_SECRET, client_secret)
        logger.info("stored TikTok application credentials")

    def forget_app_credentials(self) -> None:
        self.settings.set_secret(CLIENT_KEY_SECRET, None)
        self.settings.set_secret(CLIENT_SECRET_SECRET, None)
        self.disconnect()
        logger.info("removed TikTok application credentials")

    def require_app(self) -> tuple[str, str]:
        if not self.app_configured:
            raise AppError(
                ErrorCode.TIKTOK_APP_MISSING,
                "TikTok Client Key ve Client Secret henüz girilmedi.",
                details="secrets.json içinde tiktok_client_key / tiktok_client_secret yok",
                http_status=428,
            )
        return self.client_key, self._client_secret

    # --- files ------------------------------------------------------------

    @property
    def token_file(self) -> Path:
        return self.settings.oauth_secrets_dir / TOKEN_FILENAME

    def _read_token(self) -> dict[str, Any] | None:
        if not self.token_file.is_file():
            return None
        try:
            raw = json.loads(self.token_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("stored TikTok grant could not be read: %s", type(exc).__name__)
            return None
        return raw if isinstance(raw, dict) else None

    def _write_token(self, payload: dict[str, Any]) -> None:
        directory = self.settings.oauth_secrets_dir
        directory.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.token_file)
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass

    # --- OAuth ------------------------------------------------------------

    @property
    def redirect_uri(self) -> str:
        """Where TikTok sends the browser back.

        TikTok's developer portal requires an **HTTPS** redirect URI, which a
        loopback backend cannot offer on its own. The default below is the
        address this app can actually receive on; a user who has an HTTPS
        front-end (a tunnel, a reverse proxy) points that at the same path and
        configures it here. The app never pretends the default is registered.
        """
        configured = (self.settings.mutable.tiktok_redirect_uri or "").strip()
        if configured:
            return configured
        return f"http://localhost:{self.settings.port}/api/publishing/tiktok/callback"

    def start_authorization(self) -> OAuthStart:
        client_key, _secret = self.require_app()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)[:128]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        self._remember_state(state, verifier)

        query = urlencode(
            {
                "client_key": client_key,
                "response_type": "code",
                "scope": ",".join(SCOPES),
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return OAuthStart(
            authorization_url=f"{AUTH_HOST}/v2/auth/authorize/?{query}",
            redirect_uri=self.redirect_uri,
        )

    def _remember_state(self, state: str, verifier: str) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, pending in self._pending.items()
            if now - pending.started_at > STATE_TTL_SECONDS
        ]
        for key in stale:
            self._pending.pop(key, None)
        self._pending[state] = _PendingAuth(verifier=verifier, started_at=now)

    def _consume_state(self, state: str) -> str:
        pending = self._pending.pop(state, None)
        if pending is None or time.monotonic() - pending.started_at > STATE_TTL_SECONDS:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok bağlantısı doğrulanamadı.",
                details="beklenen 'state' değeri bulunamadı ya da süresi dolmuş",
                suggestion="“TikTok'a bağlan” düğmesine yeniden basıp işlemi baştan yapın.",
                http_status=400,
            )
        return pending.verifier

    def complete_authorization(self, code: str, state: str) -> TikTokConnection:
        verifier = self._consume_state(state)
        client_key, client_secret = self.require_app()

        payload = self._token_request(
            {
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            }
        )
        self._store_grant(payload)
        logger.info("TikTok account connected")
        return self.status()

    def _store_grant(self, payload: dict[str, Any]) -> None:
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok bir erişim izni döndürmedi.",
                details=_safe_error_detail(json.dumps(payload, ensure_ascii=False)),
                http_status=502,
            )
        expires_in = int(payload.get("expires_in") or 0)
        refresh_expires_in = int(payload.get("refresh_expires_in") or 0)
        stored = self._read_token() or {}
        self._write_token(
            {
                "accessToken": access_token,
                "refreshToken": str(payload.get("refresh_token") or "")
                or stored.get("refreshToken", ""),
                "openId": str(payload.get("open_id") or "") or stored.get("openId", ""),
                "scopes": sorted(
                    part.strip()
                    for part in str(payload.get("scope") or "").split(",")
                    if part.strip()
                )
                or stored.get("scopes", []),
                "expiresAt": (
                    (_now() + timedelta(seconds=expires_in)).isoformat() if expires_in else None
                ),
                "refreshExpiresAt": (
                    (_now() + timedelta(seconds=refresh_expires_in)).isoformat()
                    if refresh_expires_in
                    else None
                ),
                "connectedAt": stored.get("connectedAt") or _now().isoformat(),
            }
        )

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{API_HOST}/v2/oauth/token/",
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok'a ulaşılamadı.",
                details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
                http_status=504,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok beklenmedik bir yanıt döndürdü.",
                details=_safe_error_detail(response.text),
                http_status=502,
            ) from exc

        if response.status_code >= 300 or payload.get("error"):
            raise AppError(
                ErrorCode.TIKTOK_AUTH_FAILED,
                "TikTok yetkilendirmeyi kabul etmedi.",
                details=_safe_error_detail(
                    f"HTTP {response.status_code}\n"
                    f"{payload.get('error')}: {payload.get('error_description')}"
                ),
                http_status=502,
            )
        return payload

    # --- the grant --------------------------------------------------------

    def access_token(self) -> str:
        """A usable access token, refreshed when the stored one has expired."""
        stored = self._read_token()
        if stored is None:
            raise AppError(
                ErrorCode.TIKTOK_AUTH_REQUIRED,
                "TikTok hesabınız henüz bağlı değil.",
                details=f"kayıtlı yetki dosyası yok: {TOKEN_FILENAME}",
                http_status=401,
            )
        missing = self.missing_scopes(stored)
        if missing:
            raise AppError(
                ErrorCode.TIKTOK_SCOPE_MISSING,
                "TikTok bağlantınız video yayınlama iznini içermiyor.",
                details="eksik izinler:\n" + "\n".join(missing),
                http_status=403,
            )

        if not _expired(stored.get("expiresAt")):
            return str(stored.get("accessToken") or "")

        refresh_token = str(stored.get("refreshToken") or "")
        if not refresh_token or _expired(stored.get("refreshExpiresAt")):
            raise AppError(
                ErrorCode.TIKTOK_AUTH_REQUIRED,
                "TikTok bağlantınızın süresi dolmuş ve yenilenemedi.",
                details="kayıtlı yenileme izni de geçerliliğini yitirmiş",
                suggestion="Ayarlar'dan “Yeniden bağlan” düğmesine basın.",
                http_status=401,
            )
        client_key, client_secret = self.require_app()
        payload = self._token_request(
            {
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        self._store_grant(payload)
        return str(payload.get("access_token") or "")

    @staticmethod
    def missing_scopes(stored: dict[str, Any]) -> list[str]:
        granted = set(stored.get("scopes") or [])
        if not granted:
            return []
        return [scope for scope in SCOPES if scope not in granted]

    def disconnect(self) -> TikTokConnection:
        self.token_file.unlink(missing_ok=True)
        logger.info("TikTok grant removed")
        return self.status()

    # --- status -----------------------------------------------------------

    def status(self, *, refresh_creator: bool = False) -> TikTokConnection:
        """Describe the connection without ever revealing a credential."""
        stored = self._read_token()
        connection = TikTokConnection(
            app_configured=self.app_configured,
            token_present=self.token_file.is_file(),
            redirect_uri=self.redirect_uri,
            checked_at=_now(),
        )

        if not connection.app_configured:
            connection.status_message = "Uygulama bilgileri girilmedi."
            connection.problem = "TikTok Client Key ve Client Secret henüz kaydedilmemiş."
            connection.suggestion = (
                "TikTok Developer panelindeki Client Key ve Client Secret değerlerini girin."
            )
            return connection

        if stored is None:
            connection.status_message = "Uygulama bilgileri hazır, hesap henüz bağlanmadı."
            connection.suggestion = "“TikTok'a bağlan” düğmesine basın."
            return connection

        raw_expiry = stored.get("expiresAt")
        if raw_expiry:
            try:
                connection.expires_at = datetime.fromisoformat(str(raw_expiry))
            except ValueError:
                connection.expires_at = None
        connection.expired = _expired(stored.get("expiresAt")) and _expired(
            stored.get("refreshExpiresAt")
        )
        missing = self.missing_scopes(stored)
        connection.missing_scopes = missing
        connection.scopes_sufficient = not missing

        if missing:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantı var ama izinler yetersiz."
            connection.problem = "Verilen izinler video yayınlamak için yeterli değil."
            connection.suggestion = "“Yeniden bağlan” deyip video yayınlama iznini onaylayın."
            return connection
        if connection.expired:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantının süresi dolmuş."
            connection.suggestion = "“Yeniden bağlan” düğmesine basın."
            return connection

        connection.connected = True
        connection.status_message = "Bağlantı geçerli."

        cached = stored.get("creatorInfo")
        if refresh_creator or not cached:
            try:
                cached = TikTokClient(self.access_token(), self.settings).creator_info()
                stored["creatorInfo"] = cached
                self._write_token(stored)
            except AppError as exc:
                logger.info("TikTok creator info could not be read: %s", exc.code.value)
                cached = cached or None

        if cached:
            info = TikTokCreatorInfo.model_validate(cached)
            connection.creator_info = info
            connection.display_name = info.nickname or info.username or None
            connection.avatar_url = info.avatar_url
            options = {option.upper() for option in info.privacy_level_options}
            # An audited app offers more than "only me". If that is all TikTok
            # reports, the audit has not happened and the panel must say so.
            connection.audit_required = not (options - {SELF_ONLY})
            if connection.audit_required:
                connection.status_message = (
                    "Bağlantı geçerli — uygulama denetimden geçmediği için gönderiler yalnızca "
                    "sizin görebileceğiniz şekilde paylaşılır."
                )
                connection.problem = (
                    "TikTok, denetlenmemiş uygulamaların herkese açık paylaşım yapmasına izin "
                    "vermez."
                )
                connection.suggestion = (
                    "Herkese açık paylaşım için TikTok Developer panelinden Content Posting API "
                    "denetimine (audit) başvurun. O zamana kadar “Yalnızca ben” ile test edin."
                )
        return connection


def _expired(raw: Any) -> bool:
    if not raw:
        return False
    try:
        return datetime.fromisoformat(str(raw)) <= _now()
    except ValueError:
        return False


# --- API client -------------------------------------------------------------


class TikTokClient:
    """Creator info, Direct Post init, chunked upload and status polling."""

    def __init__(self, access_token: str, settings: Settings | None = None) -> None:
        self._token = access_token
        self.settings = settings or get_settings()

    def _headers(self, content_type: str = "application/json; charset=UTF-8") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": content_type,
        }

    def _post(self, path: str, body: dict[str, Any], *, stage: str) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{API_HOST}{path}",
                content=json.dumps(body).encode("utf-8"),
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.TIKTOK_API_FAILED,
                "TikTok'a ulaşılamadı.",
                details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
                http_status=504,
            ) from exc
        return _unwrap(response, stage=stage)

    # --- what this account may do ----------------------------------------

    def creator_info(self) -> dict[str, Any]:
        """Query before every post: the answer changes with audit and settings."""
        payload = self._post(
            "/v2/post/publish/creator_info/query/", {}, stage="creator-info"
        )
        data = payload.get("data") or {}
        return {
            "nickname": str(data.get("creator_nickname") or ""),
            "username": str(data.get("creator_username") or ""),
            "avatarUrl": str(data.get("creator_avatar_url") or "") or None,
            "privacyLevelOptions": [
                str(option) for option in (data.get("privacy_level_options") or [])
            ],
            "commentDisabled": bool(data.get("comment_disabled")),
            "duetDisabled": bool(data.get("duet_disabled")),
            "stitchDisabled": bool(data.get("stitch_disabled")),
            "maxVideoPostDurationSeconds": int(data.get("max_video_post_duration_sec") or 0),
            "fetchedAt": _now().isoformat(),
        }

    # --- posting ----------------------------------------------------------

    def init_direct_post(
        self,
        video: Path,
        *,
        title: str,
        privacy_level: str,
        disable_comment: bool,
        disable_duet: bool,
        disable_stitch: bool,
    ) -> tuple[str, str, int]:
        """Open a Direct Post. Returns ``(publish_id, upload_url, chunk_size)``.

        Nothing is visible on TikTok yet: this only reserves the post and tells
        the app where to send the bytes.
        """
        size = video.stat().st_size
        chunk_size, chunk_count = plan_chunks(size)
        payload = self._post(
            "/v2/post/publish/video/init/",
            {
                "post_info": {
                    "title": title,
                    "privacy_level": privacy_level,
                    "disable_comment": disable_comment,
                    "disable_duet": disable_duet,
                    "disable_stitch": disable_stitch,
                },
                "source_info": {
                    # FILE_UPLOAD, not PULL_FROM_URL: pulling would require the
                    # hosting domain to be verified with TikTok, and sending the
                    # bytes directly avoids putting the file on the internet at
                    # all.
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunk_count,
                },
            },
            stage="init",
        )
        data = payload.get("data") or {}
        publish_id = str(data.get("publish_id") or "")
        upload_url = str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise AppError(
                ErrorCode.TIKTOK_API_FAILED,
                "TikTok bir yükleme adresi döndürmedi.",
                details="init yanıtında 'publish_id' ya da 'upload_url' yok",
                http_status=502,
            )
        return publish_id, upload_url, chunk_size

    def upload_video(
        self,
        upload_url: str,
        video: Path,
        *,
        chunk_size: int,
        on_progress: Any = None,
        is_cancelled: Any = None,
    ) -> None:
        """Send the file in chunks, honouring a cancel between them."""
        total = video.stat().st_size
        sent = 0
        with video.open("rb") as handle:
            while sent < total:
                if is_cancelled is not None and is_cancelled():
                    from app.publishing.youtube import UploadCancelled

                    raise UploadCancelled()
                remaining = total - sent
                # The last chunk absorbs the remainder rather than being sent as
                # an undersized one, which TikTok rejects.
                length = remaining if remaining < 2 * chunk_size else chunk_size
                data = handle.read(length)
                if not data:
                    break
                first, last = sent, sent + len(data) - 1
                try:
                    response = httpx.put(
                        upload_url,
                        content=data,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(data)),
                            "Content-Range": f"bytes {first}-{last}/{total}",
                        },
                        timeout=UPLOAD_TIMEOUT_SECONDS,
                    )
                except httpx.HTTPError as exc:
                    raise AppError(
                        ErrorCode.TIKTOK_UPLOAD_FAILED,
                        "Video TikTok'a gönderilirken bağlantı koptu.",
                        details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
                        http_status=504,
                    ) from exc
                if response.status_code >= 300:
                    raise AppError(
                        ErrorCode.TIKTOK_UPLOAD_FAILED,
                        "TikTok video parçasını kabul etmedi.",
                        details=_safe_error_detail(
                            f"HTTP {response.status_code}\n{response.text}"
                        ),
                        http_status=502,
                    )
                sent += len(data)
                if on_progress is not None:
                    on_progress(sent, total)

    def publish_status(self, publish_id: str) -> dict[str, Any]:
        """``{status, failReason, postIds}`` for one Direct Post."""
        payload = self._post(
            "/v2/post/publish/status/fetch/", {"publish_id": publish_id}, stage="status"
        )
        data = payload.get("data") or {}
        return {
            "status": str(data.get("status") or "").upper(),
            "failReason": str(data.get("fail_reason") or ""),
            "postIds": [str(item) for item in (data.get("publicaly_available_post_id") or [])],
        }


def plan_chunks(size: int) -> tuple[int, int]:
    """TikTok's chunking rules, in one place so the init and upload agree."""
    if size <= MAX_SINGLE_CHUNK_BYTES:
        return size, 1
    chunk = max(MIN_CHUNK_BYTES, CHUNK_BYTES)
    return chunk, max(1, size // chunk)


def _unwrap(response: httpx.Response, *, stage: str) -> dict[str, Any]:
    """TikTok reports failures inside a 200 body, so the body decides."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            ErrorCode.TIKTOK_API_FAILED,
            "TikTok beklenmedik bir yanıt döndürdü.",
            details=_safe_error_detail(response.text),
            http_status=502,
        ) from exc
    if not isinstance(payload, dict):
        payload = {}

    error = payload.get("error") or {}
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or "")
    log_id = str(error.get("log_id") or "")

    if response.status_code < 300 and code in {"", "ok"}:
        return payload

    details = _safe_error_detail(
        f"HTTP {response.status_code} · {code or 'kod yok'}"
        + (f"\n{message}" if message else "")
        + (f"\nlog_id: {log_id}" if log_id else "")
    )
    raise _map_error(code, response.status_code, details, stage=stage)


def _map_error(code: str, http_status: int, details: str, *, stage: str) -> AppError:
    if code in {"access_token_invalid", "scope_not_authorized", "token_expired"} or (
        http_status == 401
    ):
        return AppError(
            ErrorCode.TIKTOK_AUTH_REQUIRED,
            "TikTok yetkiniz artık geçerli değil.",
            details=details,
            http_status=401,
        )
    if code in {"scope_permission_missed", "unaudited_client_can_only_post_to_private_accounts"}:
        return AppError(
            ErrorCode.TIKTOK_UNAUDITED,
            "TikTok bu uygulamanın herkese açık paylaşım yapmasına izin vermiyor.",
            details=details,
            http_status=403,
        )
    if code in {"privacy_level_option_mismatch", "invalid_privacy_level"}:
        return AppError(
            ErrorCode.TIKTOK_PRIVACY_NOT_ALLOWED,
            "Seçilen gizlilik bu hesap için kullanılamıyor.",
            details=details,
            http_status=422,
        )
    if code in {"rate_limit_exceeded", "spam_risk_too_many_posts"} or http_status == 429:
        return AppError(
            ErrorCode.TIKTOK_API_FAILED,
            "TikTok gönderi sınırına ulaşıldığını bildirdi.",
            details=details,
            suggestion="Bir süre bekleyip tekrar deneyin.",
            http_status=429,
        )
    if stage == "upload":
        return AppError(
            ErrorCode.TIKTOK_UPLOAD_FAILED,
            "Video TikTok'a gönderilemedi.",
            details=details,
            http_status=502,
        )
    return AppError(
        ErrorCode.TIKTOK_API_FAILED,
        "TikTok bir hata döndürdü.",
        details=details,
        http_status=502,
    )
