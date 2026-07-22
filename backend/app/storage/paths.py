"""Filesystem path safety.

Every path that originates from user input — project names, uploaded filenames,
values read out of an imported project.json — passes through here before it
touches the disk or an FFmpeg argument list.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.errors import AppError, ErrorCode

#: Characters that are always stripped from a filename component.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_DASH = re.compile(r"-{2,}")
_LEADING_JUNK = re.compile(r"^[._-]+")

#: Windows reserved device names, rejected so bundles stay portable.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

MAX_COMPONENT_LENGTH = 96


def slugify(value: str, *, fallback: str = "untitled") -> str:
    """Turn arbitrary text into a safe, lowercase, single path component.

    Accents are folded rather than dropped so "Réunion Ibis" becomes
    "reunion-ibis" instead of "runion-ibis".
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _UNSAFE.sub("-", ascii_only).strip("-_.").lower()
    cleaned = _MULTI_DASH.sub("-", cleaned)
    cleaned = _LEADING_JUNK.sub("", cleaned)
    cleaned = cleaned[:MAX_COMPONENT_LENGTH].rstrip("-_.")
    if not cleaned or cleaned.split(".")[0] in _RESERVED:
        return fallback
    return cleaned


def sanitize_filename(name: str, *, default_stem: str = "file", default_suffix: str = "") -> str:
    """Sanitize an uploaded filename while preserving a sensible extension.

    ``../../etc/passwd`` collapses to ``passwd``; ``photo (1).PNG`` becomes
    ``photo-1.png``. The extension is kept because downstream code and FFmpeg
    both rely on it for format detection.
    """
    # Take only the final component: defeats both / and \ traversal attempts.
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    path = Path(base)
    suffix = path.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = default_suffix
        stem = base
    else:
        stem = path.stem
    safe_stem = slugify(stem, fallback=default_stem)
    return f"{safe_stem}{suffix}"


def safe_join(root: Path, *parts: str | Path) -> Path:
    """Join ``parts`` under ``root``, refusing anything that escapes it.

    Both the root and the result are fully resolved, so symlinks that point
    outside the project are rejected too.
    """
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*[str(p) for p in parts])
    # strict=False: the target may not exist yet (we are often creating it).
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise AppError(
            ErrorCode.PATH_TRAVERSAL,
            "Bu dosya yolu proje klasörünün dışını gösteriyor ve kabul edilmedi.",
            details=f"root={resolved_root}\nrequested={candidate}\nresolved={resolved}",
            http_status=400,
        )
    return resolved


def is_within(root: Path, candidate: Path) -> bool:
    """Non-raising variant of :func:`safe_join`'s containment check."""
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=False)
    return resolved == resolved_root or resolved_root in resolved.parents


def unique_path(directory: Path, stem: str, suffix: str, *, width: int = 2) -> Path:
    """Return ``<stem>_v01<suffix>``, incrementing until the name is free.

    Used for exports so a re-render never silently destroys a previous one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    version = 1
    while True:
        candidate = directory / f"{stem}_v{version:0{width}d}{suffix}"
        if not candidate.exists():
            return candidate
        version += 1
        if version > 9999:
            raise AppError(
                ErrorCode.EXPORT_EXISTS,
                f"'{stem}' için boş bir dosya adı bulunamadı (9999 deneme).",
                details=f"directory={directory}",
            )


def natural_sort_key(name: str) -> tuple[object, ...]:
    """Sort key that orders ``2-x`` before ``10-x`` (plain string sort does not).

    Used to map uploaded images onto scenes in filename order.
    """
    parts = re.split(r"(\d+)", name.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts if p != "")
