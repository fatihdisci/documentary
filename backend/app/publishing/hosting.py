"""Temporary media hosting, for the platforms that fetch a video themselves.

YouTube and TikTok take the bytes: the app streams the file to them and nothing
leaves this computer that is not part of that upload. Instagram and Facebook do
not work that way. Their publishing APIs accept a **URL** and download the video
from it, which means a local-first app has to park the file somewhere reachable
for a few minutes.

This module is that "somewhere", and it is deliberately small:

* :class:`ObjectStorageHost` puts the object in any S3-compatible bucket —
  Cloudflare R2 is the one the docs walk through — and hands out a **presigned**
  GET link that expires. The bucket stays private; the link is the only way in,
  it is signed with a deadline, and the object is deleted again once the
  platform has taken it.
* When nothing is configured, :func:`resolve_media_host` raises an error that
  says exactly what to set up. It never invents a URL, never falls back to
  something that would silently fail, and never sends Meta a ``file://`` path.

The signing is SigV4, written out here rather than pulled in with an SDK: the
app needs exactly two operations (PUT an object, sign a GET) and an AWS SDK is a
large dependency for that. Everything it needs — SHA-256, HMAC, a canonical
request — is in the standard library.

Nothing in this module logs a key, a signature or a signed URL.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, EnvironmentError_
from app.publishing.models import MediaHostStatus

logger = logging.getLogger("evb.publishing.hosting")

#: Secret keys for the bucket. Stored in ``secrets.json`` (0600) like every
#: other key the app holds, and never returned by an endpoint.
ACCESS_KEY_SECRET = "object_storage_access_key_id"
SECRET_KEY_SECRET = "object_storage_secret_access_key"

#: Uploading a whole Reel over a slow line takes a while; a short timeout here
#: would abort a perfectly healthy transfer.
UPLOAD_TIMEOUT_SECONDS = 900.0
DELETE_TIMEOUT_SECONDS = 30.0

_UNSIGNED = "UNSIGNED-PAYLOAD"


@dataclass(frozen=True)
class HostedMedia:
    """One file, temporarily reachable at ``url`` until ``expires_at``."""

    url: str
    #: The key inside the bucket, kept so the object can be removed later.
    object_key: str
    expires_at: datetime

    @property
    def safe_description(self) -> str:
        """Something loggable. The signed URL itself never is."""
        return f"{self.object_key} (geçerlilik {self.expires_at.isoformat()})"


class MediaHost:
    """What the publishing jobs need from a hosting backend."""

    def put(self, path: Path, *, key_hint: str) -> HostedMedia:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, object_key: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


# --- S3 / R2 ----------------------------------------------------------------


class ObjectStorageHost(MediaHost):
    """An S3-compatible bucket, addressed path-style so R2 works unchanged."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        mutable = self.settings.mutable
        self.endpoint = mutable.object_storage_endpoint.rstrip("/")
        self.bucket = mutable.object_storage_bucket.strip()
        self.region = (mutable.object_storage_region or "auto").strip()
        self.prefix = mutable.object_storage_prefix.strip("/")
        self.ttl_seconds = max(300, int(mutable.media_host_ttl_seconds or 3600))
        self._access_key = self.settings.get_secret(ACCESS_KEY_SECRET) or ""
        self._secret_key = self.settings.get_secret(SECRET_KEY_SECRET) or ""

    # --- public API -------------------------------------------------------

    def put(self, path: Path, *, key_hint: str) -> HostedMedia:
        """Upload the file and return a presigned link to it.

        The key carries a random component, so two uploads of the same export
        never collide and a link cannot be guessed from the filename.
        """
        self._require_configuration()
        key = self._object_key(path, key_hint)
        content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        url = self._object_url(key)
        headers = self._signed_headers(
            "PUT",
            key,
            payload_hash=_UNSIGNED,
            extra={
                "content-type": content_type,
                "content-length": str(path.stat().st_size),
            },
        )

        try:
            with path.open("rb") as handle:
                response = httpx.put(
                    url, content=handle, headers=headers, timeout=UPLOAD_TIMEOUT_SECONDS
                )
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.MEDIA_HOST_FAILED,
                "Video geçici barındırma alanına yüklenemedi.",
                details=_safe_detail(f"{type(exc).__name__}: {exc}"),
                http_status=502,
            ) from exc

        if response.status_code >= 300:
            raise AppError(
                ErrorCode.MEDIA_HOST_FAILED,
                "Geçici barındırma alanı videoyu kabul etmedi.",
                details=_safe_detail(f"HTTP {response.status_code}\n{response.text}"),
                http_status=502,
            )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        logger.info("parked a video for Meta to fetch: %s", key)
        return HostedMedia(
            url=self.presign_get(key), object_key=key, expires_at=expires_at
        )

    def delete(self, object_key: str) -> None:
        """Remove the temporary object. Never fatal: the link expires anyway."""
        if not object_key:
            return
        self._require_configuration()
        try:
            response = httpx.delete(
                self._object_url(object_key),
                headers=self._signed_headers("DELETE", object_key, payload_hash=_EMPTY_SHA),
                timeout=DELETE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("temporary object %s could not be deleted: %s", object_key, exc)
            return
        if response.status_code >= 300 and response.status_code != 404:
            logger.warning(
                "temporary object %s was not deleted (HTTP %s)", object_key, response.status_code
            )
        else:
            logger.info("removed the temporary copy of %s", object_key)

    def presign_get(self, key: str) -> str:
        """A time-limited GET link. This is the only thing Meta ever sees."""
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        host = self._host()

        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self._access_key}/{scope}",
            "X-Amz-Date": stamp,
            "X-Amz-Expires": str(self.ttl_seconds),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(
            f"{_quote(name)}={_quote(value)}" for name, value in sorted(query.items())
        )
        canonical_request = "\n".join(
            [
                "GET",
                self._canonical_path(key),
                canonical_query,
                f"host:{host}\n",
                "host",
                _UNSIGNED,
            ]
        )
        signature = self._sign(canonical_request, stamp=stamp, datestamp=datestamp, scope=scope)
        return f"{self._object_url(key)}?{canonical_query}&X-Amz-Signature={signature}"

    # --- signing ----------------------------------------------------------

    def _host(self) -> str:
        return self.endpoint.split("://", 1)[-1].split("/", 1)[0]

    def _object_url(self, key: str) -> str:
        return f"{self.endpoint}/{self.bucket}/{_quote(key, safe='/')}"

    def _canonical_path(self, key: str) -> str:
        return f"/{self.bucket}/{_quote(key, safe='/')}"

    def _object_key(self, path: Path, key_hint: str) -> str:
        stem = "".join(
            character if character.isalnum() or character in "-_." else "-"
            for character in (key_hint or path.stem)
        ).strip("-")[:60] or "video"
        suffix = path.suffix.lower() or ".mp4"
        parts = [part for part in (self.prefix, f"{stem}-{uuid.uuid4().hex[:12]}{suffix}") if part]
        return "/".join(parts)

    def _signed_headers(
        self, method: str, key: str, *, payload_hash: str, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        scope = f"{datestamp}/{self.region}/s3/aws4_request"

        headers = {
            "host": self._host(),
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": stamp,
            **{name.lower(): value for name, value in (extra or {}).items()},
        }
        signed_names = ";".join(sorted(headers))
        canonical_headers = "".join(
            f"{name}:{headers[name].strip()}\n" for name in sorted(headers)
        )
        canonical_request = "\n".join(
            [
                method,
                self._canonical_path(key),
                "",
                canonical_headers,
                signed_names,
                payload_hash,
            ]
        )
        signature = self._sign(canonical_request, stamp=stamp, datestamp=datestamp, scope=scope)
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}"
        )
        return headers

    def _sign(self, canonical_request: str, *, stamp: str, datestamp: str, scope: str) -> str:
        to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                stamp,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key = f"AWS4{self._secret_key}".encode("utf-8")
        for part in (datestamp, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
        return hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # --- configuration ----------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.bucket and self._access_key and self._secret_key)

    def _require_configuration(self) -> None:
        missing = [
            label
            for label, value in (
                ("Endpoint adresi", self.endpoint),
                ("Kova (bucket) adı", self.bucket),
                ("Access Key ID", self._access_key),
                ("Secret Access Key", self._secret_key),
            )
            if not value
        ]
        if missing:
            raise EnvironmentError_(
                ErrorCode.MEDIA_HOST_NOT_CONFIGURED,
                "Geçici medya barındırma ayarları eksik.",
                details="eksik alanlar: " + ", ".join(missing),
            )


_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _quote(value: str, *, safe: str = "") -> str:
    return quote(value, safe=safe)


def _safe_detail(text: str, *, limit: int = 1500) -> str:
    """Trim, and drop anything that looks like a signature, before showing it."""
    cleaned = text
    for marker in ("X-Amz-Signature=", "Signature=", "X-Amz-Credential="):
        head, sep, _tail = cleaned.partition(marker)
        if sep:
            cleaned = f"{head}{marker}[gizlendi]"
    return cleaned[:limit]


# --- resolution and status --------------------------------------------------


def resolve_media_host(settings: Settings | None = None) -> MediaHost:
    """The configured host, or an error that says what to configure.

    There is no fallback on purpose. Guessing at a URL Meta cannot reach would
    turn a setup problem into a failed publish several minutes later.
    """
    settings = settings or get_settings()
    provider = (settings.mutable.media_host_provider or "none").strip().lower()
    if provider in {"s3", "r2"}:
        host = ObjectStorageHost(settings)
        host._require_configuration()
        return host
    raise EnvironmentError_(
        ErrorCode.MEDIA_HOST_NOT_CONFIGURED,
        "Instagram ve Facebook için geçici medya barındırma tanımlanmamış.",
        details=(
            "Meta, videoyu bir adresten kendisi indirir; bilgisayarınızdaki dosya yolunu "
            "kullanamaz. Ayarlardan S3/R2 uyumlu bir kova tanımlayın."
        ),
    )


def media_host_status(settings: Settings | None = None) -> MediaHostStatus:
    """Describe the hosting setup without revealing either key."""
    settings = settings or get_settings()
    mutable = settings.mutable
    provider = (mutable.media_host_provider or "none").strip().lower()
    keys_present = bool(
        settings.get_secret(ACCESS_KEY_SECRET) and settings.get_secret(SECRET_KEY_SECRET)
    )

    status = MediaHostStatus(
        provider=provider,
        endpoint=mutable.object_storage_endpoint,
        bucket=mutable.object_storage_bucket,
        region=mutable.object_storage_region,
        prefix=mutable.object_storage_prefix,
        keys_present=keys_present,
        ttl_seconds=mutable.media_host_ttl_seconds,
        delete_after_publish=mutable.media_host_delete_after_publish,
    )

    if provider not in {"s3", "r2"}:
        status.status_message = "Tanımlı değil."
        status.problem = (
            "Instagram ve Facebook'a yükleme yapabilmek için geçici bir barındırma alanı gerekir."
        )
        status.suggestion = (
            "Cloudflare R2 ya da S3 uyumlu bir kova oluşturup bilgilerini buraya girin."
        )
        return status

    missing = [
        label
        for label, value in (
            ("endpoint", mutable.object_storage_endpoint),
            ("kova adı", mutable.object_storage_bucket),
            ("anahtarlar", "x" if keys_present else ""),
        )
        if not value
    ]
    if missing:
        status.status_message = "Eksik ayar var."
        status.problem = "Şunlar tanımlanmamış: " + ", ".join(missing) + "."
        status.suggestion = "Eksik alanları doldurup kaydedin."
        return status

    status.configured = True
    status.status_message = (
        f"Hazır — {mutable.object_storage_bucket} kovası, bağlantılar "
        f"{mutable.media_host_ttl_seconds // 60} dakika geçerli."
    )
    return status
