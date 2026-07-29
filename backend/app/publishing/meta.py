"""The only module in the app that talks to Meta (Instagram and Facebook).

One connection, two destinations. The same Facebook Login grant yields a Page
access token, and that single token is what publishes a Reel to the Page *and*,
through the Page's linked Instagram professional account, a Reel to Instagram.
So the credential work lives here once, and the two publishing paths sit beside
each other rather than in two half-duplicated modules.

Three responsibilities, kept apart:

* :class:`MetaCredentials` owns the grant — storing the App ID and App Secret,
  building the authorization URL, exchanging the code, upgrading to a
  long-lived token, discovering the Pages and their Instagram accounts, and
  reporting what state the connection is in.
* :class:`MetaClient` owns the API calls — creating an Instagram Reel container,
  waiting for it to finish processing, publishing it, and the three-phase
  Facebook Reel upload.
* :func:`map_graph_error` turns Meta's error bodies into something the user can
  act on, with the credential-shaped parts removed.

Everything here is **blocking**. Callers on the event loop go through
``anyio.to_thread.run_sync``; the job worker does exactly that.

Security rules this module enforces, not merely follows:

* The App Secret, the user token and the Page tokens are never returned, logged
  or put in an error payload. Meta's own error bodies are filtered through
  :func:`_safe_error_detail` first.
* The App ID appears in exactly one place — the authorization URL, where OAuth
  requires it — and in no status response.
* The token file is written 0600, atomically, into the app's secrets directory.
* "Disconnect" deletes the token file. The stored App ID and App Secret stay,
  because re-connecting should not mean a trip back to the Meta panel.
"""

from __future__ import annotations

import hashlib
import hmac
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
from app.errors import AppError, ErrorCode, NotFoundError, ValidationError
from app.publishing.models import (
    MetaConnection,
    MetaPageSummary,
    OAuthStart,
)

logger = logging.getLogger("evb.publishing.meta")

#: Exactly the permissions the two publishing paths need, and no more.
#:
#: ``instagram_content_publish`` is what allows the container/publish pair;
#: ``pages_manage_posts`` is what allows a Reel on the Page. The other four are
#: read permissions needed to find *which* Page and *which* Instagram account
#: the grant covers — without them the app would have to ask the user to type
#: ids by hand, which is exactly the kind of guessing this app avoids.
SCOPES: tuple[str, ...] = (
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
    "pages_manage_posts",
)

APP_ID_SECRET = "meta_app_id"
APP_SECRET_SECRET = "meta_app_secret"

TOKEN_FILENAME = "meta-token.json"

GRAPH_HOST = "https://graph.facebook.com"
LOGIN_HOST = "https://www.facebook.com"

#: A code is exchanged within seconds of the redirect; anything older is a
#: replay or a stale browser tab and is refused.
STATE_TTL_SECONDS = 600

REQUEST_TIMEOUT_SECONDS = 60.0

#: Meta transcodes a Reel before it can be published. This is the ceiling on how
#: long the job waits, not an expectation — most finish in well under a minute.
CONTAINER_TIMEOUT_SECONDS = 15 * 60
CONTAINER_POLL_SECONDS = 5.0

#: Anything credential-shaped is scrubbed from text that leaves this module.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(access_token|client_secret|app_secret|appsecret_proof|code)"
               r"\"?\s*[:=]\s*\"?[^\s\",&}]+"),
    re.compile(r"(?i)authorization:\s*\S+"),
    re.compile(r"EAA[A-Za-z0-9]{20,}"),
)


def _safe_error_detail(text: str, *, limit: int = 2000) -> str:
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[gizlendi]", cleaned)
    return cleaned[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- stored state -----------------------------------------------------------


@dataclass(frozen=True)
class MetaTarget:
    """Everything one publish needs, resolved from the stored grant."""

    page_id: str
    page_name: str
    page_token: str
    instagram_id: str | None
    instagram_username: str | None

    def require_instagram(self) -> str:
        if not self.instagram_id:
            raise AppError(
                ErrorCode.META_INSTAGRAM_NOT_LINKED,
                f"'{self.page_name}' sayfasına bağlı bir Instagram profesyonel hesabı yok.",
                details="sayfanın instagram_business_account alanı boş döndü",
                http_status=409,
            )
        return self.instagram_id


class MetaCredentials:
    """Stores the app credentials and the grant, and reports the connection."""

    #: Pending OAuth states, per process. A connect and its callback are seconds
    #: apart in the same run, so this never needs to survive a restart — and a
    #: state that does not survive one cannot be replayed after one either.
    _pending: dict[str, float] = {}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- app credentials --------------------------------------------------

    @property
    def app_id(self) -> str:
        return (self.settings.get_secret(APP_ID_SECRET) or "").strip()

    @property
    def _app_secret(self) -> str:
        return (self.settings.get_secret(APP_SECRET_SECRET) or "").strip()

    @property
    def app_configured(self) -> bool:
        return bool(self.app_id and self._app_secret)

    def store_app_credentials(self, app_id: str, app_secret: str, *, replace: bool) -> None:
        """Save the App ID and App Secret. Neither is ever readable again.

        Refuses to overwrite an existing pair unless the caller asked for it, so
        a stray save from a half-filled form cannot silently break a working
        connection.
        """
        app_id = (app_id or "").strip()
        app_secret = (app_secret or "").strip()

        if self.app_configured and not replace:
            raise AppError(
                ErrorCode.META_APP_INVALID,
                "Meta uygulama bilgileri zaten kayıtlı.",
                details="mevcut kayıt korunmak için değiştirilmedi",
                suggestion=(
                    "Değiştirmek istiyorsanız önce “Kimlik bilgilerini değiştir” seçeneğini "
                    "işaretleyin."
                ),
                http_status=409,
            )

        if not app_id.isdigit() or not 8 <= len(app_id) <= 32:
            raise ValidationError(
                ErrorCode.META_APP_INVALID,
                "Meta App ID yalnızca rakamlardan oluşan bir numaradır.",
                details=f"girilen değerin uzunluğu: {len(app_id)}",
            )
        if len(app_secret) < 16 or not re.fullmatch(r"[A-Za-z0-9]+", app_secret):
            raise ValidationError(
                ErrorCode.META_APP_INVALID,
                "Meta App Secret beklenen biçimde değil.",
                details="App Secret harf ve rakamlardan oluşan uzun bir dizedir",
                suggestion=(
                    "Meta Developer panelinde App settings → Basic → App secret altındaki "
                    "değeri “Show” deyip olduğu gibi kopyalayın."
                ),
            )

        self.settings.set_secret(APP_ID_SECRET, app_id)
        self.settings.set_secret(APP_SECRET_SECRET, app_secret)
        logger.info("stored Meta application credentials")

    def forget_app_credentials(self) -> None:
        self.settings.set_secret(APP_ID_SECRET, None)
        self.settings.set_secret(APP_SECRET_SECRET, None)
        self.disconnect()
        logger.info("removed Meta application credentials")

    def require_app(self) -> tuple[str, str]:
        if not self.app_configured:
            raise AppError(
                ErrorCode.META_APP_MISSING,
                "Meta App ID ve App Secret henüz girilmedi.",
                details="secrets.json içinde meta_app_id / meta_app_secret yok",
                http_status=428,
            )
        return self.app_id, self._app_secret

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
            logger.warning("stored Meta grant could not be read: %s", type(exc).__name__)
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
        """Where Meta sends the browser back. Must match the app's settings.

        Defaults to this backend's own callback on loopback, which is what a
        desktop app can actually receive. Meta accepts a ``localhost`` redirect
        while the app is in Development mode — which is exactly the mode this
        integration is designed for.
        """
        configured = (self.settings.mutable.meta_redirect_uri or "").strip()
        if configured:
            return configured
        return f"http://localhost:{self.settings.port}/api/publishing/meta/callback"

    def start_authorization(self) -> OAuthStart:
        """Build the URL to open in the user's browser."""
        app_id, _secret = self.require_app()
        state = secrets.token_urlsafe(24)
        self._remember_state(state)
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": ",".join(SCOPES),
                # Always show the picker: a user with several Pages or several
                # Instagram accounts must be able to choose, and a silent
                # re-grant would quietly keep the wrong one.
                "auth_type": "rerequest",
            }
        )
        return OAuthStart(
            authorization_url=f"{LOGIN_HOST}/{self._version}/dialog/oauth?{query}",
            redirect_uri=self.redirect_uri,
        )

    def _remember_state(self, state: str) -> None:
        now = time.monotonic()
        expired = [key for key, born in self._pending.items() if now - born > STATE_TTL_SECONDS]
        for key in expired:
            self._pending.pop(key, None)
        self._pending[state] = now

    def _consume_state(self, state: str) -> None:
        born = self._pending.pop(state, None)
        if born is None or time.monotonic() - born > STATE_TTL_SECONDS:
            raise AppError(
                ErrorCode.META_AUTH_FAILED,
                "Meta bağlantısı doğrulanamadı.",
                details="beklenen 'state' değeri bulunamadı ya da süresi dolmuş",
                suggestion="“Meta'ya bağlan” düğmesine yeniden basıp işlemi baştan yapın.",
                http_status=400,
            )

    def complete_authorization(self, code: str, state: str) -> MetaConnection:
        """Exchange the code, upgrade the token, and discover the accounts."""
        self._consume_state(state)
        app_id, app_secret = self.require_app()

        short_lived = self._get(
            "/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
            stage="token",
        )
        access_token = str(short_lived.get("access_token") or "")
        if not access_token:
            raise AppError(
                ErrorCode.META_AUTH_FAILED,
                "Meta bir erişim izni döndürmedi.",
                details="yanıtta 'access_token' yok",
                http_status=502,
            )

        # A short-lived token dies in about an hour, which would make the panel
        # useless by tomorrow. The long-lived exchange is what makes the
        # connection last ~60 days.
        long_lived = self._get(
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": access_token,
            },
            stage="token",
        )
        user_token = str(long_lived.get("access_token") or access_token)
        expires_in = int(long_lived.get("expires_in") or 0)
        expires_at = (_now() + timedelta(seconds=expires_in)) if expires_in else None

        granted = self._granted_scopes(user_token)
        pages = self._discover_pages(user_token)
        if not pages:
            raise NotFoundError(
                ErrorCode.META_PAGE_NOT_FOUND,
                "Bu Meta hesabının yöneticisi olduğu bir Facebook Sayfası bulunamadı.",
                details="/me/accounts boş döndü",
            )

        payload = {
            "userToken": user_token,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "scopes": granted,
            "pages": pages,
            # One Page is not a choice, so making the user confirm it would be
            # ceremony. Several is, and stays unselected until they pick.
            "selectedPageId": pages[0]["id"] if len(pages) == 1 else None,
            "connectedAt": _now().isoformat(),
        }
        self._write_token(payload)
        logger.info("Meta account connected (%d page(s) available)", len(pages))
        return self.status()

    def _granted_scopes(self, user_token: str) -> list[str]:
        """What Meta actually granted, which can be less than what was asked."""
        try:
            response = self._get(
                "/me/permissions", params={"access_token": user_token}, stage="permissions"
            )
        except AppError:
            # Not knowing the scope list must not fail the whole connect; the
            # first publish will report a missing permission precisely.
            logger.info("Meta permission list could not be read")
            return []
        return sorted(
            str(item.get("permission"))
            for item in (response.get("data") or [])
            if isinstance(item, dict) and item.get("status") == "granted"
        )

    def _discover_pages(self, user_token: str) -> list[dict[str, Any]]:
        """Every Page the user administers, with its linked Instagram account."""
        response = self._get(
            "/me/accounts",
            params={
                "access_token": user_token,
                "fields": (
                    "id,name,access_token,"
                    "instagram_business_account{id,username}"
                ),
                "limit": 100,
            },
            stage="pages",
        )
        pages: list[dict[str, Any]] = []
        for item in response.get("data") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            instagram = item.get("instagram_business_account") or {}
            pages.append(
                {
                    "id": str(item["id"]),
                    "name": str(item.get("name") or ""),
                    "accessToken": str(item.get("access_token") or ""),
                    "instagramId": str(instagram.get("id") or "") or None,
                    "instagramUsername": str(instagram.get("username") or "") or None,
                }
            )
        return pages

    def select_page(self, page_id: str) -> MetaConnection:
        stored = self._read_token()
        if stored is None:
            raise AppError(
                ErrorCode.META_AUTH_REQUIRED,
                "Meta hesabınız henüz bağlı değil.",
                details=f"kayıtlı yetki dosyası yok: {TOKEN_FILENAME}",
                http_status=401,
            )
        if not any(page.get("id") == page_id for page in stored.get("pages") or []):
            raise NotFoundError(
                ErrorCode.META_PAGE_NOT_FOUND,
                f"'{page_id}' numaralı bir Facebook Sayfası bu bağlantıda yok.",
                details="seçim yalnızca bağlantının kapsadığı sayfalar arasından yapılabilir",
            )
        stored["selectedPageId"] = page_id
        self._write_token(stored)
        return self.status()

    def disconnect(self) -> MetaConnection:
        """Delete the stored grant. The App ID and App Secret are kept."""
        self.token_file.unlink(missing_ok=True)
        logger.info("Meta grant removed")
        return self.status()

    # --- what a publish needs --------------------------------------------

    def target(self) -> MetaTarget:
        """The selected Page and its Instagram account, or an actionable error."""
        stored = self._read_token()
        if stored is None:
            raise AppError(
                ErrorCode.META_AUTH_REQUIRED,
                "Meta hesabınız henüz bağlı değil.",
                details=f"kayıtlı yetki dosyası yok: {TOKEN_FILENAME}",
                http_status=401,
            )
        if self._expired(stored):
            raise AppError(
                ErrorCode.META_AUTH_REQUIRED,
                "Meta bağlantınızın süresi dolmuş.",
                details="kayıtlı yetkinin geçerlilik tarihi geçmiş",
                suggestion="Ayarlar'dan “Yeniden bağlan” düğmesine basın.",
                http_status=401,
            )

        missing = self.missing_scopes(stored)
        if missing:
            raise AppError(
                ErrorCode.META_SCOPE_MISSING,
                "Meta bağlantınız yayınlama için gereken izinleri içermiyor.",
                details="eksik izinler:\n" + "\n".join(missing),
                http_status=403,
            )

        pages = stored.get("pages") or []
        selected = stored.get("selectedPageId")
        page = next((item for item in pages if item.get("id") == selected), None)
        if page is None:
            raise AppError(
                ErrorCode.META_PAGE_NOT_FOUND,
                "Hangi Facebook Sayfasına yayınlanacağı seçilmemiş.",
                details=f"bağlantıdaki sayfa sayısı: {len(pages)}",
                suggestion="Ayarlar → Meta bağlantısı bölümünden sayfayı seçin.",
                http_status=409,
            )
        token = str(page.get("accessToken") or "")
        if not token:
            raise AppError(
                ErrorCode.META_AUTH_REQUIRED,
                "Bu sayfa için kayıtlı bir yetki yok.",
                details="sayfa kaydında erişim izni bulunamadı",
                http_status=401,
            )
        return MetaTarget(
            page_id=str(page["id"]),
            page_name=str(page.get("name") or ""),
            page_token=token,
            instagram_id=page.get("instagramId"),
            instagram_username=page.get("instagramUsername"),
        )

    @staticmethod
    def _expired(stored: dict[str, Any]) -> bool:
        raw = stored.get("expiresAt")
        if not raw:
            return False
        try:
            return datetime.fromisoformat(str(raw)) <= _now()
        except ValueError:
            return False

    @staticmethod
    def missing_scopes(stored: dict[str, Any]) -> list[str]:
        granted = set(stored.get("scopes") or [])
        # An empty list means the permission lookup failed, not that nothing was
        # granted. Reporting every scope as missing there would be a lie.
        if not granted:
            return []
        return [scope for scope in SCOPES if scope not in granted]

    # --- status -----------------------------------------------------------

    def status(self) -> MetaConnection:
        """Describe the connection without ever revealing a credential."""
        stored = self._read_token()
        connection = MetaConnection(
            app_configured=self.app_configured,
            token_present=self.token_file.is_file(),
            redirect_uri=self.redirect_uri,
            checked_at=_now(),
        )

        if not connection.app_configured:
            connection.status_message = "Uygulama bilgileri girilmedi."
            connection.problem = "Meta App ID ve App Secret henüz kaydedilmemiş."
            connection.suggestion = (
                "Meta Developer panelindeki App ID ve App Secret değerlerini aşağıya bir kez girin."
            )
            return connection

        if stored is None:
            connection.status_message = "Uygulama bilgileri hazır, hesap henüz bağlanmadı."
            connection.suggestion = "“Meta'ya bağlan” düğmesine basın."
            return connection

        raw_expiry = stored.get("expiresAt")
        if raw_expiry:
            try:
                connection.expires_at = datetime.fromisoformat(str(raw_expiry))
            except ValueError:
                connection.expires_at = None
        connection.expired = self._expired(stored)

        connection.pages = [
            MetaPageSummary(
                page_id=str(page.get("id") or ""),
                name=str(page.get("name") or ""),
                instagram_id=page.get("instagramId"),
                instagram_username=page.get("instagramUsername"),
            )
            for page in stored.get("pages") or []
        ]
        selected = stored.get("selectedPageId")
        connection.selected_page_id = str(selected) if selected else None
        chosen = next(
            (page for page in connection.pages if page.page_id == connection.selected_page_id),
            None,
        )
        if chosen is not None:
            connection.page_name = chosen.name
            connection.instagram_id = chosen.instagram_id
            connection.instagram_username = chosen.instagram_username

        missing = self.missing_scopes(stored)
        connection.missing_scopes = missing
        connection.scopes_sufficient = not missing

        if missing:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantı var ama izinler yetersiz."
            connection.problem = "Verilen izinler yayınlama için yeterli değil."
            connection.suggestion = (
                "“Yeniden bağlan” deyip Meta'nın izin ekranındaki tüm kutuları işaretleyin."
            )
        elif connection.expired:
            connection.needs_reconnect = True
            connection.status_message = "Bağlantının süresi dolmuş."
            connection.problem = "Meta yetkisi 60 günde bir yenilenmelidir."
            connection.suggestion = "“Yeniden bağlan” düğmesine basın."
        elif chosen is None:
            connection.status_message = "Bağlantı geçerli, sayfa seçilmedi."
            connection.problem = "Birden fazla Facebook Sayfası bulundu."
            connection.suggestion = "Yayın yapılacak sayfayı aşağıdan seçin."
        else:
            connection.connected = True
            if chosen.instagram_id:
                connection.status_message = (
                    f"Bağlantı geçerli — {chosen.name} sayfası ve "
                    f"@{chosen.instagram_username} Instagram hesabı."
                )
            else:
                connection.status_message = (
                    f"Bağlantı geçerli — {chosen.name} sayfası. Instagram hesabı bağlı değil."
                )
                connection.problem = (
                    "Bu sayfaya bağlı bir Instagram profesyonel hesabı görünmüyor."
                )
                connection.suggestion = (
                    "Instagram hesabınızın Business/Creator olduğundan ve bu sayfaya bağlı "
                    "olduğundan emin olup yeniden bağlanın."
                )
        return connection

    # --- HTTP -------------------------------------------------------------

    @property
    def _version(self) -> str:
        return (self.settings.mutable.meta_graph_version or "v21.0").strip()

    def _get(self, path: str, *, params: dict[str, Any], stage: str) -> dict[str, Any]:
        return graph_request(
            "GET", f"{GRAPH_HOST}/{self._version}{path}", params=params, stage=stage
        )


# --- API client -------------------------------------------------------------


class MetaClient:
    """The Instagram and Facebook publishing calls, and nothing else."""

    def __init__(self, target: MetaTarget, settings: Settings | None = None) -> None:
        self.target = target
        self.settings = settings or get_settings()
        self._version = (self.settings.mutable.meta_graph_version or "v21.0").strip()
        self._proof = _appsecret_proof(
            target.page_token, MetaCredentials(self.settings)._app_secret
        )

    def _url(self, path: str) -> str:
        return f"{GRAPH_HOST}/{self._version}/{path.lstrip('/')}"

    def _auth(self) -> dict[str, str]:
        params = {"access_token": self.target.page_token}
        if self._proof:
            params["appsecret_proof"] = self._proof
        return params

    # --- Instagram --------------------------------------------------------

    def create_reel_container(
        self, *, video_url: str, caption: str, share_to_feed: bool
    ) -> str:
        """Ask Instagram to ingest the video. Returns the container id.

        Creating a container publishes nothing. It is the point at which Meta
        downloads the file from ``video_url`` and starts transcoding, and it is
        reversible — an abandoned container simply expires.
        """
        instagram_id = self.target.require_instagram()
        response = graph_request(
            "POST",
            self._url(f"{instagram_id}/media"),
            params={
                **self._auth(),
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true" if share_to_feed else "false",
            },
            stage="ig-container",
        )
        container_id = str(response.get("id") or "")
        if not container_id:
            raise AppError(
                ErrorCode.META_API_FAILED,
                "Instagram bir yükleme numarası döndürmedi.",
                details="media yanıtında 'id' yok",
                http_status=502,
            )
        return container_id

    def container_status(self, container_id: str) -> tuple[str, str | None]:
        """``(status_code, error)`` for one container.

        ``FINISHED`` means Meta has the video and it is ready to publish, not
        that anything is visible yet.
        """
        response = graph_request(
            "GET",
            self._url(container_id),
            params={**self._auth(), "fields": "status_code,status"},
            stage="ig-status",
        )
        code = str(response.get("status_code") or "").upper()
        detail = response.get("status")
        return code, str(detail) if detail else None

    def publish_container(self, container_id: str) -> str:
        """The irreversible call: the Reel becomes a post. Returns its media id."""
        instagram_id = self.target.require_instagram()
        response = graph_request(
            "POST",
            self._url(f"{instagram_id}/media_publish"),
            params={**self._auth(), "creation_id": container_id},
            stage="ig-publish",
        )
        media_id = str(response.get("id") or "")
        if not media_id:
            raise AppError(
                ErrorCode.META_API_FAILED,
                "Instagram yayınlanan gönderinin numarasını döndürmedi.",
                details="media_publish yanıtında 'id' yok",
                http_status=502,
            )
        return media_id

    def media_permalink(self, media_id: str) -> str:
        """The public link. A failure here is never fatal — the post exists."""
        try:
            response = graph_request(
                "GET",
                self._url(media_id),
                params={**self._auth(), "fields": "permalink"},
                stage="ig-permalink",
            )
        except AppError:
            logger.info("Instagram permalink lookup failed for %s", media_id)
            return ""
        return str(response.get("permalink") or "")

    # --- Facebook ---------------------------------------------------------

    def start_page_reel(self) -> tuple[str, str]:
        """Open a Reel upload session. Returns ``(video_id, upload_url)``.

        A started session holds a video id that is not yet a post. That id is
        what a retry resumes from, which is why it is persisted on the job.
        """
        response = graph_request(
            "POST",
            self._url(f"{self.target.page_id}/video_reels"),
            params={**self._auth(), "upload_phase": "start"},
            stage="fb-start",
        )
        video_id = str(response.get("video_id") or "")
        upload_url = str(response.get("upload_url") or "")
        if not video_id or not upload_url:
            raise AppError(
                ErrorCode.META_API_FAILED,
                "Facebook yükleme oturumu açılamadı.",
                details="yanıtta 'video_id' ya da 'upload_url' yok",
                http_status=502,
            )
        return video_id, upload_url

    def upload_page_reel(self, upload_url: str, *, video_url: str) -> None:
        """Hand Facebook the link and let it pull the file itself.

        The same hosted URL Instagram used, so one temporary object serves both
        platforms and the file is never uploaded twice.
        """
        try:
            response = httpx.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {self.target.page_token}",
                    "file_url": video_url,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.META_API_FAILED,
                "Facebook'a video adresi iletilemedi.",
                details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
                http_status=504,
            ) from exc
        if response.status_code >= 300:
            raise map_graph_error(response, stage="fb-upload")

    def finish_page_reel(self, video_id: str, *, description: str) -> None:
        """Publish the Reel on the Page. Irreversible."""
        graph_request(
            "POST",
            self._url(f"{self.target.page_id}/video_reels"),
            params={
                **self._auth(),
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description,
            },
            stage="fb-finish",
        )

    def page_reel_status(self, video_id: str) -> tuple[str, str | None]:
        """``(video_status, error)`` while Facebook processes the Reel."""
        response = graph_request(
            "GET",
            self._url(video_id),
            params={**self._auth(), "fields": "status"},
            stage="fb-status",
        )
        status = response.get("status") or {}
        if not isinstance(status, dict):
            return "", None
        video_status = str(status.get("video_status") or "").lower()
        processing = status.get("processing_phase") or {}
        error = None
        if isinstance(processing, dict) and processing.get("errors"):
            error = json.dumps(processing["errors"], ensure_ascii=False)[:500]
        return video_status, error

    def page_reel_permalink(self, video_id: str) -> str:
        try:
            response = graph_request(
                "GET",
                self._url(video_id),
                params={**self._auth(), "fields": "permalink_url"},
                stage="fb-permalink",
            )
        except AppError:
            logger.info("Facebook permalink lookup failed for %s", video_id)
            return ""
        permalink = str(response.get("permalink_url") or "")
        if permalink.startswith("/"):
            return f"https://www.facebook.com{permalink}"
        return permalink


def _appsecret_proof(access_token: str, app_secret: str) -> str:
    """Meta's tamper check on a token. Empty when the secret is unavailable."""
    if not access_token or not app_secret:
        return ""
    return hmac.new(
        app_secret.encode("utf-8"), access_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# --- transport and error mapping --------------------------------------------


def graph_request(
    method: str, url: str, *, params: dict[str, Any], stage: str
) -> dict[str, Any]:
    """One Graph call. Parameters go in the body for writes, never in a log."""
    try:
        if method == "GET":
            response = httpx.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        else:
            # POST bodies keep the token out of the request line, which is the
            # part proxies and server logs are most likely to record.
            response = httpx.post(url, data=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise AppError(
            ErrorCode.META_API_FAILED,
            "Meta'ya ulaşılamadı.",
            details=_safe_error_detail(f"{type(exc).__name__}: {exc}"),
            http_status=504,
        ) from exc

    if response.status_code >= 300:
        raise map_graph_error(response, stage=stage)

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            ErrorCode.META_API_FAILED,
            "Meta beklenmedik bir yanıt döndürdü.",
            details=_safe_error_detail(response.text),
            http_status=502,
        ) from exc
    return payload if isinstance(payload, dict) else {"data": payload}


#: Graph error codes worth telling apart. Everything else is a generic failure
#: with Meta's own message attached.
_TOKEN_CODES = {102, 190, 463, 467}
_PERMISSION_CODES = {10, 200, 803}
_RATE_CODES = {4, 17, 32, 613, 80007}
_MEDIA_CODES = {2207026, 2207020, 2207032, 2207003, 9004}


def map_graph_error(response: httpx.Response, *, stage: str) -> AppError:
    """Turn a Graph error response into something the user can act on."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = (payload.get("error") or {}) if isinstance(payload, dict) else {}
    code = int(error.get("code") or 0)
    subcode = int(error.get("error_subcode") or 0)
    message = str(error.get("message") or "").strip()
    user_message = str(error.get("error_user_msg") or "").strip()

    details = _safe_error_detail(
        f"HTTP {response.status_code} · kod {code}"
        + (f"/{subcode}" if subcode else "")
        + (f"\n{message}" if message else "")
        + (f"\n{user_message}" if user_message else "")
    )

    stage_label = {
        "token": "Yetki alınırken",
        "permissions": "İzinler okunurken",
        "pages": "Sayfalar okunurken",
        "ig-container": "Instagram Reels hazırlanırken",
        "ig-status": "Instagram işleme durumu okunurken",
        "ig-publish": "Instagram'da yayınlanırken",
        "ig-permalink": "Instagram bağlantısı okunurken",
        "fb-start": "Facebook yükleme oturumu açılırken",
        "fb-upload": "Facebook'a video iletilirken",
        "fb-finish": "Facebook'ta yayınlanırken",
        "fb-status": "Facebook işleme durumu okunurken",
        "fb-permalink": "Facebook bağlantısı okunurken",
    }.get(stage, "Meta işlemi sırasında")

    if code in _TOKEN_CODES or response.status_code == 401:
        return AppError(
            ErrorCode.META_AUTH_REQUIRED,
            "Meta yetkiniz artık geçerli değil.",
            details=details,
            http_status=401,
        )
    if code in _PERMISSION_CODES or response.status_code == 403:
        return AppError(
            ErrorCode.META_SCOPE_MISSING,
            "Bu işlem için Meta izinleriniz yetersiz.",
            details=details,
            http_status=403,
        )
    if code in _RATE_CODES or response.status_code == 429:
        return AppError(
            ErrorCode.META_RATE_LIMITED,
            "Meta bu hesap için yayın sınırına ulaşıldığını bildirdi.",
            details=details,
            http_status=429,
        )
    if code in _MEDIA_CODES or subcode in _MEDIA_CODES:
        return AppError(
            ErrorCode.META_MEDIA_REJECTED,
            "Meta video dosyasını kabul etmedi.",
            details=details,
            http_status=422,
        )
    return AppError(
        ErrorCode.META_API_FAILED,
        f"{stage_label} Meta bir hata döndürdü.",
        details=details,
        http_status=502,
    )
