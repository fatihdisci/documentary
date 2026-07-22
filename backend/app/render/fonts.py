"""Font resolution.

Fonts are **bundled**, not assumed to exist on the machine. That is what makes
the rendered text identical everywhere, and it is the reason this app does not
need FFmpeg to have been built with libfreetype.

System fonts are still offered as an option, but never as a silent fallback for
a font the project explicitly asked for — a substitution changes line breaks and
therefore the layout, so it is always reported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from app.errors import AppError, ErrorCode

logger = logging.getLogger("evb.fonts")

BUNDLED_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

#: Weight -> bundled file. Requests land on the nearest available weight.
_INTER_WEIGHTS: dict[int, str] = {
    300: "Inter-Light.ttf",
    400: "Inter-Regular.ttf",
    500: "Inter-Medium.ttf",
    600: "Inter-SemiBold.ttf",
    700: "Inter-Bold.ttf",
    900: "Inter-Black.ttf",
}

#: Directories searched when a project names a font we do not bundle.
_SYSTEM_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
)


@dataclass(frozen=True)
class ResolvedFont:
    family: str
    weight: int
    path: Path
    bundled: bool
    #: Set when the request could not be met exactly.
    substitution_note: str = ""


#: Fonts that exist on macOS but are useless for documentary titling. Offering
#: them in a picker is just noise.
_EXCLUDED_KEYWORDS = (
    "braille", "emoji", "symbol", "dingbat", "webding", "wingding", "icons",
    "notonastaliq", "kana", "cjk", "geeza", "nastaleeq", "mshtakan", "lastresort",
    "hiragino", "applesd", "adtnumeric", "keyboard", "system",
)


def available_families() -> list[str]:
    """Families this installation can render with, bundled ones first.

    Filtered to fonts that make sense for on-screen titling.
    """
    families = ["Inter"] if _bundled_present() else []
    seen: set[str] = set()
    for directory in _SYSTEM_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.tt[fc]")) + sorted(directory.glob("*.otf")):
            stem = path.stem.split("-")[0].strip()
            lowered = stem.lower().replace(" ", "")
            if (
                stem in seen
                or stem == "Inter"
                or stem.startswith(".")
                or any(keyword in lowered for keyword in _EXCLUDED_KEYWORDS)
            ):
                continue
            seen.add(stem)
            families.append(stem)
    return families


def _bundled_present() -> bool:
    return any((BUNDLED_DIR / name).is_file() for name in _INTER_WEIGHTS.values())


def resolve(family: str, weight: int = 400) -> ResolvedFont:
    """Find a usable font file for ``family`` at (or near) ``weight``.

    Never raises for a missing family: it falls back to bundled Inter and says
    so, because failing a whole render over a font name is not a good trade.
    """
    requested = (family or "Inter").strip()

    if requested.lower() in {"inter", ""}:
        return _bundled_inter(weight)

    system = _find_system_font(requested, weight)
    if system is not None:
        return system

    fallback = _bundled_inter(weight)
    logger.warning("font %r not found; falling back to bundled Inter", requested)
    return ResolvedFont(
        family=fallback.family,
        weight=fallback.weight,
        path=fallback.path,
        bundled=True,
        substitution_note=(
            f"'{requested}' yazı tipi bu bilgisayarda kurulu değil; onun yerine uygulamayla "
            "gelen Inter kullanıldı. Yazı boyutları ve satır sonları farklı olabilir."
        ),
    )


def _bundled_inter(weight: int) -> ResolvedFont:
    if not _bundled_present():
        raise AppError(
            ErrorCode.FONT_UNAVAILABLE,
            "Uygulamayla gelen yazı tipleri bulunamadı.",
            details=f"Inter .ttf dosyaları burada olmalıydı: {BUNDLED_DIR}",
            suggestion=(
                "Uygulamayı yeniden kurun ya da Inter-Regular.ttf ve Inter-Bold.ttf "
                f"dosyalarını şuraya koyun: {BUNDLED_DIR}."
            ),
        )
    nearest = min(_INTER_WEIGHTS, key=lambda w: abs(w - weight))
    path = BUNDLED_DIR / _INTER_WEIGHTS[nearest]
    if not path.is_file():
        # The exact weight is missing but others exist; use the closest present.
        for candidate_weight in sorted(_INTER_WEIGHTS, key=lambda w: abs(w - weight)):
            candidate = BUNDLED_DIR / _INTER_WEIGHTS[candidate_weight]
            if candidate.is_file():
                path, nearest = candidate, candidate_weight
                break
    return ResolvedFont(family="Inter", weight=nearest, path=path, bundled=True)


def _find_system_font(family: str, weight: int) -> ResolvedFont | None:
    """Look for a system font matching family and roughly the right weight."""
    weight_names = _weight_keywords(weight)
    normalized = family.lower().replace(" ", "")

    best: tuple[int, Path] | None = None
    for directory in _SYSTEM_DIRS:
        if not directory.is_dir():
            continue
        for path in list(directory.glob("*.tt[fc]")) + list(directory.glob("*.otf")):
            stem = path.stem.lower().replace(" ", "")
            if not stem.startswith(normalized):
                continue
            score = 0 if any(kw in stem for kw in weight_names) else 1
            if best is None or score < best[0]:
                best = (score, path)
    if best is None:
        return None
    return ResolvedFont(family=family, weight=weight, path=best[1], bundled=False)


def _weight_keywords(weight: int) -> tuple[str, ...]:
    if weight >= 800:
        return ("black", "heavy")
    if weight >= 650:
        return ("bold",)
    if weight >= 550:
        return ("semibold", "demibold")
    if weight >= 450:
        return ("medium",)
    if weight <= 350:
        return ("light",)
    return ("regular", "book")


@lru_cache(maxsize=64)
def load(family: str, weight: int, size: int) -> ImageFont.FreeTypeFont:
    """Load a PIL font. Cached: text cards re-resolve the same fonts constantly."""
    resolved = resolve(family, weight)
    try:
        return ImageFont.truetype(str(resolved.path), size)
    except OSError as exc:
        raise AppError(
            ErrorCode.FONT_UNAVAILABLE,
            f"'{family}' yazı tipi dosyası açılamadı.",
            details=f"{resolved.path}: {exc}",
            suggestion="Görünüm ayarlarından başka bir yazı tipi seçin.",
        ) from exc


def validate(family: str) -> tuple[bool, str]:
    """Check a family without raising. Returns (exact_match, message)."""
    try:
        resolved = resolve(family, 400)
    except AppError as exc:
        return False, exc.message
    if resolved.substitution_note:
        return False, resolved.substitution_note
    source = "uygulamayla geliyor" if resolved.bundled else f"sistemde: {resolved.path}"
    return True, f"'{resolved.family}' kullanılabilir ({source})."
