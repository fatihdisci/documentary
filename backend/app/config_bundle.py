"""Moving one installation's settings and credentials to another computer.

Everything this app knows that is *not* a project lives in three places::

    ~/ExtinctVideoBuilder/settings.json   preferences                (readable)
    ~/ExtinctVideoBuilder/secrets.json    API keys and app secrets    (0600)
    ~/ExtinctVideoBuilder/secrets/        OAuth client files, tokens  (0600)

Setting all of that up a second time means another trip through Google Cloud and
TikTok's console. This module packs it into one file instead.

**The file is always encrypted, and the passphrase is never optional.** That is
not caution for its own sake: the bundle contains every API key and every OAuth
refresh token this installation holds, and it is going to sit on a USB stick, in
a Downloads folder, or in a sync folder on the way to the other machine. A
plaintext export would undo every other rule in this codebase about credentials
never touching disk unprotected.

Shape of the file — a small readable header, then one sealed blob::

    {"format": "evb-settings-bundle", "version": 1,
     "kdf": {...}, "cipher": "AES-256-GCM", "nonce": ..., "contents": {...},
     "ciphertext": "<base64>"}

The header is *authenticated* (it is the AEAD's associated data), so editing the
counts or the KDF parameters invalidates the whole file rather than silently
changing how it is opened. The header carries counts and a date and nothing
else: no key name, no service name, no hostname.

Two things this module deliberately does not do:

* it never writes a decrypted bundle to disk — the plaintext exists only as
  bytes in memory during an import;
* it never logs, returns or echoes a secret *value*. Import reports the **names**
  of what it restored, which is what a person needs to confirm the transfer
  worked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets as secrets_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field

from app.config import MutableSettings, Settings, get_settings
from app.errors import AppError, ErrorCode, ValidationError  # noqa: F401 - AppError is caught
from app.models.base import CamelModel

logger = logging.getLogger("evb.config.bundle")

FORMAT = "evb-settings-bundle"
VERSION = 1

#: The extension is deliberately not ``.json``: a bundle must never look like a
#: config file someone can open, skim and commit.
FILE_SUFFIX = ".evbkey"

#: scrypt at 32 MB. Strong enough that a weak passphrase still costs real time
#: to attack, cheap enough that unlocking is instant on the user's own machine.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12

#: Short enough not to be annoying, long enough that scrypt is doing real work.
MIN_PASSPHRASE_CHARS = 8

#: A bundle is a few kilobytes. Anything larger is not one.
MAX_BUNDLE_BYTES = 4 * 1_048_576

#: Files inside ``secrets/`` worth carrying over, by exact name or glob. An
#: allowlist rather than "everything in the folder", so a stray file someone
#: dropped there is never swept into an export.
CREDENTIAL_PATTERNS = (
    "client_secret_*.json",
    "oauth-client-*.json",
    "youtube-upload-token.json",
    "tiktok-token.json",
)

#: Settings that describe *this computer*, not the user's preferences. Exported
#: for completeness, but not applied on import unless asked for: a path from the
#: other machine that does not exist here would leave the app looking for
#: projects in a folder that is not there.
MACHINE_SPECIFIC_FIELDS = (
    "ffmpeg_path",
    "ffprobe_path",
    "projects_dir",
    "exports_dir",
    "temp_dir",
)


# --- wire models ------------------------------------------------------------


class BundleExportRequest(CamelModel):
    """Ask for an export. The passphrase is used and immediately forgotten."""

    passphrase: str
    #: Include the OAuth grants, so the other computer does not have to redo
    #: every browser sign-in. On by default because that is the whole point.
    include_credentials: bool = True


class BundleContents(CamelModel):
    """What a bundle holds. Counts only — never a key name or a service name."""

    settings: bool = False
    secrets: int = 0
    credential_files: int = 0
    created_at: datetime | None = None


class BundleImportResult(CamelModel):
    """What an import actually did, by name. Never a value."""

    settings_applied: bool = False
    #: Fields deliberately left alone, with the reason, so nothing is silently
    #: dropped.
    skipped_settings: list[str] = Field(default_factory=list)
    secrets_imported: list[str] = Field(default_factory=list)
    secrets_skipped: list[str] = Field(default_factory=list)
    credential_files_imported: list[str] = Field(default_factory=list)
    credential_files_skipped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    contents: BundleContents = Field(default_factory=BundleContents)


# --- crypto -----------------------------------------------------------------


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=SCRYPT_N * SCRYPT_R * 256,
    )


def _require_passphrase(passphrase: str) -> str:
    value = passphrase or ""
    if len(value) < MIN_PASSPHRASE_CHARS:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_PASSPHRASE,
            f"Parola en az {MIN_PASSPHRASE_CHARS} karakter olmalı.",
            details=f"girilen uzunluk: {len(value)}",
            suggestion=(
                "Bu dosya bütün anahtarlarınızı taşıyor; hatırlayabileceğiniz ama tahmin "
                "edilemeyecek bir parola seçin."
            ),
        )
    return value


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: Any, *, field: str) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Dosya bozuk görünüyor.",
            details=f"'{field}' alanı okunamadı",
        ) from exc


# --- export -----------------------------------------------------------------


def export_bundle(
    request: BundleExportRequest, settings: Settings | None = None
) -> tuple[bytes, str, BundleContents]:
    """Pack settings, secrets and credential files into one sealed file.

    Returns ``(file bytes, suggested filename, contents)``. The contents are the
    same counts that go in the header, so the caller can report them without
    opening anything.
    """
    settings = settings or get_settings()
    passphrase = _require_passphrase(request.passphrase)

    stored_secrets = _read_all_secrets(settings)
    credential_files = (
        _read_credential_files(settings) if request.include_credentials else {}
    )

    payload = {
        "settings": settings.mutable.model_dump(by_alias=True, mode="json"),
        "secrets": stored_secrets,
        "credentialFiles": credential_files,
    }
    contents = BundleContents(
        settings=True,
        secrets=len(stored_secrets),
        credential_files=len(credential_files),
        created_at=datetime.now(timezone.utc),
    )

    salt = secrets_module.token_bytes(SALT_BYTES)
    nonce = secrets_module.token_bytes(NONCE_BYTES)
    header = {
        "format": FORMAT,
        "version": VERSION,
        "cipher": "AES-256-GCM",
        "kdf": {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _b64(salt),
        },
        "nonce": _b64(nonce),
        "contents": contents.model_dump(by_alias=True, mode="json"),
    }
    # The header is the AEAD's associated data, so a bundle whose counts or KDF
    # parameters were edited fails to open rather than opening differently.
    associated = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_key(passphrase, salt)
    sealed = AESGCM(key).encrypt(
        nonce, json.dumps(payload, ensure_ascii=False).encode("utf-8"), associated
    )

    document = {**header, "ciphertext": _b64(sealed)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    logger.info(
        "exported a settings bundle (%d secret(s), %d credential file(s))",
        contents.secrets,
        contents.credential_files,
    )
    return (
        json.dumps(document, indent=2).encode("utf-8"),
        f"evb-ayarlar-{stamp}{FILE_SUFFIX}",
        contents,
    )


def _read_all_secrets(settings: Settings) -> dict[str, str]:
    """Every stored secret, by name. Values are read once and never logged."""
    return {
        name: value
        for name in settings.secret_names()
        if (value := settings.get_secret(name))
    }


def _read_credential_files(settings: Settings) -> dict[str, str]:
    """OAuth client files and grants, by basename.

    The channel cache is left out on purpose: it is derived data that the other
    machine re-fetches in a second, and there is no reason to carry it.
    """
    directory = settings.oauth_secrets_dir
    if not directory.is_dir():
        return {}
    found: dict[str, str] = {}
    for pattern in CREDENTIAL_PATTERNS:
        for path in sorted(directory.glob(pattern)):
            if not path.is_file() or path.name in found:
                continue
            try:
                found[path.name] = path.read_text("utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning("credential file %s could not be read for export", path.name)
    return found


# --- import -----------------------------------------------------------------


def read_bundle_header(data: bytes) -> BundleContents:
    """The counts and the date, without needing the passphrase.

    Lets the panel say "5 anahtar, 3 yetki dosyası, 28.07.2026" before asking
    the user to type anything, and lets a wrong file be rejected immediately
    rather than as a confusing "wrong passphrase".
    """
    document = _parse_document(data)
    raw = document.get("contents")
    if not isinstance(raw, dict):
        return BundleContents()
    try:
        return BundleContents.model_validate(raw)
    except ValueError:
        return BundleContents()


def import_bundle(
    data: bytes,
    passphrase: str,
    *,
    overwrite: bool = True,
    include_paths: bool = False,
    settings: Settings | None = None,
) -> BundleImportResult:
    """Open a bundle and put its contents back where they belong."""
    settings = settings or get_settings()
    passphrase = _require_passphrase(passphrase)
    document = _parse_document(data)
    payload = _decrypt(document, passphrase)

    result = BundleImportResult(contents=read_bundle_header(data))
    _apply_settings(settings, payload.get("settings"), include_paths, result)
    _apply_secrets(settings, payload.get("secrets"), overwrite, result)
    _apply_credential_files(settings, payload.get("credentialFiles"), overwrite, result)

    logger.info(
        "imported a settings bundle (%d secret(s), %d credential file(s))",
        len(result.secrets_imported),
        len(result.credential_files_imported),
    )
    return result


def _parse_document(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Bu dosya bir ayar paketi için fazla büyük.",
            details=f"limit: {MAX_BUNDLE_BYTES} bayt",
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Seçtiğiniz dosya bir ayar paketi değil.",
            details="dosya geçerli bir JSON belgesi değil",
            suggestion=f"Dışa aktarmayla oluşturulmuş bir {FILE_SUFFIX} dosyası seçin.",
        ) from exc

    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Seçtiğiniz dosya bir ayar paketi değil.",
            details="dosyanın 'format' alanı beklenen değeri taşımıyor",
            suggestion=f"Dışa aktarmayla oluşturulmuş bir {FILE_SUFFIX} dosyası seçin.",
        )
    if int(document.get("version") or 0) > VERSION:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Bu paket uygulamanın daha yeni bir sürümüyle oluşturulmuş.",
            details=f"paket sürümü {document.get('version')}, bu sürüm en fazla {VERSION}",
            suggestion="Bu bilgisayardaki uygulamayı güncelleyin.",
        )
    return document


def _decrypt(document: dict[str, Any], passphrase: str) -> dict[str, Any]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kdf = document.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Paketin şifreleme bilgisi okunamadı.",
            details="beklenen anahtar türetme yöntemi: scrypt",
        )

    salt = _unb64(kdf.get("salt"), field="kdf.salt")
    nonce = _unb64(document.get("nonce"), field="nonce")
    ciphertext = _unb64(document.get("ciphertext"), field="ciphertext")

    header = {key: value for key, value in document.items() if key != "ciphertext"}
    associated = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    try:
        key = _derive_key(
            passphrase,
            salt,
        )
        opened = AESGCM(key).decrypt(nonce, ciphertext, associated)
    except InvalidTag as exc:
        # One message for both causes on purpose: the tag cannot tell "wrong
        # passphrase" from "edited file", and guessing would mislead.
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_PASSPHRASE,
            "Parola yanlış ya da dosya değiştirilmiş.",
            details="şifre çözme doğrulaması başarısız",
            suggestion="Dışa aktarırken kullandığınız parolayı girin.",
        ) from exc
    except ValueError as exc:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Paket açılamadı.",
            details=f"{type(exc).__name__}",
        ) from exc

    try:
        payload = json.loads(opened.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            ErrorCode.SETTINGS_BUNDLE_INVALID,
            "Paketin içeriği okunamadı.",
            details="çözülen içerik geçerli bir JSON belgesi değil",
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _apply_settings(
    settings: Settings,
    raw: Any,
    include_paths: bool,
    result: BundleImportResult,
) -> None:
    if not isinstance(raw, dict):
        return
    try:
        incoming = MutableSettings.model_validate(raw)
    except ValueError as exc:
        result.warnings.append(
            "Paketteki tercihler bu sürümle uyuşmadığı için atlandı."
        )
        logger.info("bundle settings could not be validated: %s", exc)
        return

    current = settings.mutable
    if not include_paths:
        # Keep this machine's own paths, field by field, rather than adopting
        # the other one's — a folder that exists there usually does not here.
        incoming = incoming.model_copy(
            update={field: getattr(current, field) for field in MACHINE_SPECIFIC_FIELDS}
        )
        result.skipped_settings.extend(MACHINE_SPECIFIC_FIELDS)
        result.warnings.append(
            "Klasör ve program yolları bu bilgisayarınkiler olarak bırakıldı; diğer "
            "bilgisayardaki yollar burada bulunmayabilir."
        )

    settings.save_mutable(incoming)
    result.settings_applied = True

    # Creating the folders is a courtesy, not part of the transfer. A path that
    # cannot be created here — the other machine's external drive, most likely —
    # must be reported, not allowed to abort an import that has already restored
    # the keys the user came for.
    try:
        settings.ensure_dirs()
    except AppError as exc:
        result.warnings.append(
            f"{exc.message} Ayarlar → Dosya konumları bölümünden bu bilgisayarda var olan "
            "bir klasör seçin."
        )
        logger.info("bundle import could not prepare a directory: %s", exc.code.value)


def _apply_secrets(
    settings: Settings, raw: Any, overwrite: bool, result: BundleImportResult
) -> None:
    if not isinstance(raw, dict):
        return
    existing = set(settings.secret_names())
    for name, value in sorted(raw.items()):
        if not isinstance(value, str) or not value:
            continue
        if name in existing and not overwrite:
            result.secrets_skipped.append(name)
            continue
        settings.set_secret(name, value)
        result.secrets_imported.append(name)


def _apply_credential_files(
    settings: Settings, raw: Any, overwrite: bool, result: BundleImportResult
) -> None:
    if not isinstance(raw, dict):
        return
    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:  # pragma: no cover - platform dependent
        pass

    for name, text in sorted(raw.items()):
        if not isinstance(text, str):
            continue
        # The name comes from a file the user is importing, so it is treated as
        # untrusted: only the allowlisted shapes are written, and only ever
        # directly inside the secrets directory.
        if not _is_allowed_credential_name(name):
            result.credential_files_skipped.append(name)
            continue
        target = directory / name
        if target.exists() and not overwrite:
            result.credential_files_skipped.append(name)
            continue
        _write_private(target, text)
        result.credential_files_imported.append(name)


def _is_allowed_credential_name(name: str) -> bool:
    from fnmatch import fnmatch

    if not name or "/" in name or "\\" in name or name != Path(name).name:
        return False
    if name.startswith("."):
        return False
    return any(fnmatch(name, pattern) for pattern in CREDENTIAL_PATTERNS)


def _write_private(target: Path, text: str) -> None:
    """Write atomically and owner-only, exactly as the OAuth modules do."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.chmod(0o600)
    tmp.replace(target)
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
