"""Image and audio asset handling.

Uploaded originals are validated, sanitized and stored untouched. Everything the
renderer needs — normalized working copies, thumbnails — is derived into
``derived/`` and can be rebuilt at any time.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import ErrorCode, ValidationError
from app.models.base import CamelModel
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join, sanitize_filename

logger = logging.getLogger("evb.media")

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

#: Below this, pan-and-zoom visibly softens the picture.
MIN_IMAGE_WIDTH = 1280
MIN_IMAGE_HEIGHT = 720

#: Guards against decompression-bomb images.
MAX_IMAGE_PIXELS = 80_000_000

THUMBNAIL_WIDTH = 480


class ImageInfo(CamelModel):
    filename: str
    width: int
    height: int
    format: str
    size_bytes: int
    aspect_ratio: float
    thumbnail_url: str | None = None
    #: Set when the image is usable but not ideal (e.g. unusual aspect ratio).
    warnings: list[str] = []


class MusicTrack(CamelModel):
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class StoredImage:
    path: Path
    info: ImageInfo


def validate_image_bytes(data: bytes, original_name: str) -> tuple[Image.Image, str]:
    """Decode and validate an uploaded image. Returns the image and its format.

    Raises a specific, actionable error for every rejection reason.
    """
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_IMAGE,
            f"'{original_name}' desteklenmeyen bir görsel türü ({suffix or 'uzantısız'}).",
            details=f"supported: {', '.join(sorted(SUPPORTED_IMAGE_SUFFIXES))}",
        )

    import io

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # force full decode: catches truncated files
    except UnidentifiedImageError as exc:
        raise ValidationError(
            ErrorCode.CORRUPT_IMAGE,
            f"'{original_name}' bir görsel olarak açılamadı.",
            details=str(exc),
        ) from exc
    except Image.DecompressionBombError as exc:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_IMAGE,
            f"'{original_name}' aşırı büyük (en fazla {MAX_IMAGE_PIXELS:,} piksel).",
            details=str(exc),
            suggestion="Görseli küçültüp tekrar yükleyin.",
        ) from exc
    except OSError as exc:
        raise ValidationError(
            ErrorCode.CORRUPT_IMAGE,
            f"'{original_name}' eksik ya da bozuk görünüyor.",
            details=str(exc),
            suggestion="Görseli yeniden kaydedip tekrar yükleyin.",
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    image_format = (image.format or suffix.lstrip(".")).upper()

    if image.width < MIN_IMAGE_WIDTH or image.height < MIN_IMAGE_HEIGHT:
        raise ValidationError(
            ErrorCode.IMAGE_TOO_SMALL,
            f"'{original_name}' {image.width}x{image.height} boyutunda; yakınlaşma "
            "hareketiyle 1080p bir video için fazla küçük.",
            details=f"en az: {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT}",
            suggestion=(
                f"En az {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT} boyutunda bir görsel kullanın. "
                "2560x1440 ve üzeri gözle görülür şekilde daha iyi sonuç verir."
            ),
        )

    return image, image_format


def store_image(paths: ProjectPaths, data: bytes, original_name: str, *, slug: str) -> StoredImage:
    """Validate and save an uploaded image, then build its thumbnail."""
    image, image_format = validate_image_bytes(data, original_name)
    filename = _unique_filename(paths.images, sanitize_filename(original_name, default_stem="image"))

    paths.images.mkdir(parents=True, exist_ok=True)
    target = safe_join(paths.images, filename)
    target.write_bytes(data)  # the original, byte for byte

    info = _describe(image, filename, len(data), image_format, slug=slug)
    _write_thumbnail(paths, image, filename)
    logger.info("stored image %s (%dx%d) in %s", filename, image.width, image.height, paths.root.name)
    return StoredImage(path=target, info=info)


def _describe(
    image: Image.Image, filename: str, size_bytes: int, image_format: str, *, slug: str
) -> ImageInfo:
    aspect = image.width / image.height
    warnings: list[str] = []
    # 16:9 is 1.778. Anything far from it gets cropped noticeably.
    if aspect < 1.2:
        warnings.append(
            f"Bu görsel {'dikey' if aspect < 1.0 else 'neredeyse kare'} "
            f"({image.width}x{image.height}). Geniş ekrana sığdırmak için epeyce kırpılacak — "
            "odak noktasını ayarlayın ki asıl konu kadraj dışında kalmasın."
        )
    elif aspect > 2.6:
        warnings.append(
            f"Bu görsel çok geniş ({image.width}x{image.height}); geniş ekrana sığdırmak "
            "için yanlardan kırpılacak."
        )
    if image.width < 1920 or image.height < 1080:
        warnings.append(
            f"{image.width}x{image.height} boyutu 1080p'nin altında; yakınlaşınca görüntü "
            "bulanıklaşacak."
        )

    return ImageInfo(
        filename=filename,
        width=image.width,
        height=image.height,
        format=image_format,
        size_bytes=size_bytes,
        aspect_ratio=round(aspect, 4),
        thumbnail_url=f"/api/projects/{slug}/media/thumbnails/{Path(filename).stem}.jpg",
        warnings=warnings,
    )


def _write_thumbnail(paths: ProjectPaths, image: Image.Image, filename: str) -> Path:
    paths.thumbnails.mkdir(parents=True, exist_ok=True)
    target = paths.thumbnails / f"{Path(filename).stem}.jpg"
    thumb = ImageOps.exif_transpose(image)
    thumb = thumb.convert("RGB")
    ratio = THUMBNAIL_WIDTH / thumb.width
    thumb = thumb.resize((THUMBNAIL_WIDTH, max(1, round(thumb.height * ratio))), Image.LANCZOS)
    thumb.save(target, "JPEG", quality=82, optimize=True)
    return target


def rebuild_thumbnail(paths: ProjectPaths, filename: str) -> Path:
    source = safe_join(paths.images, filename)
    if not source.is_file():
        raise ValidationError(
            ErrorCode.MISSING_IMAGE,
            f"'{filename}' görseli bu projede yok.",
            details=str(source),
        )
    with Image.open(source) as image:
        image.load()
        return _write_thumbnail(paths, image, filename)


def list_images(paths: ProjectPaths, *, slug: str) -> list[ImageInfo]:
    """Describe every image currently in the project, cheapest way possible."""
    if not paths.images.is_dir():
        return []
    results: list[ImageInfo] = []
    for path in sorted(paths.images.iterdir()):
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES or not path.is_file():
            continue
        try:
            with Image.open(path) as image:
                results.append(
                    _describe(image, path.name, path.stat().st_size, (image.format or "").upper(), slug=slug)
                )
        except (UnidentifiedImageError, OSError) as exc:
            logger.warning("skipping unreadable image %s: %s", path, exc)
    return results


def delete_image(paths: ProjectPaths, filename: str) -> None:
    target = safe_join(paths.images, filename)
    if not target.is_file():
        raise ValidationError(
            ErrorCode.MISSING_IMAGE,
            f"'{filename}' görseli bu projede yok.",
        )
    target.unlink()
    (paths.thumbnails / f"{Path(filename).stem}.jpg").unlink(missing_ok=True)
    # Normalized working copies referencing this image are now stale.
    for stale in paths.normalized.glob(f"{Path(filename).stem}*"):
        stale.unlink(missing_ok=True)


def validate_audio_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_AUDIO,
            f"'{original_name}' desteklenmeyen bir ses türü ({suffix or 'uzantısız'}).",
            details=f"desteklenenler: {', '.join(sorted(SUPPORTED_AUDIO_SUFFIXES))}",
        )
    return sanitize_filename(original_name, default_stem="audio")


def store_imported_audio(paths: ProjectPaths, data: bytes, original_name: str) -> Path:
    """Save user-supplied narration audio. Never overwritten by the renderer."""
    filename = _unique_filename(paths.imported_audio, validate_audio_name(original_name))
    paths.imported_audio.mkdir(parents=True, exist_ok=True)
    target = safe_join(paths.imported_audio, filename)
    target.write_bytes(data)
    logger.info("stored imported audio %s in %s", filename, paths.root.name)
    return target


def store_music(paths: ProjectPaths, data: bytes, original_name: str) -> Path:
    filename = _unique_filename(paths.music, validate_audio_name(original_name))
    paths.music.mkdir(parents=True, exist_ok=True)
    target = safe_join(paths.music, filename)
    target.write_bytes(data)
    logger.info("stored music %s in %s", filename, paths.root.name)
    return target


def list_music(paths: ProjectPaths) -> list[MusicTrack]:
    """Every uploaded track in the project's ``music/`` folder."""
    if not paths.music.is_dir():
        return []
    tracks: list[MusicTrack] = []
    for path in sorted(paths.music.iterdir()):
        if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES or not path.is_file():
            continue
        tracks.append(MusicTrack(filename=path.name, size_bytes=path.stat().st_size))
    return tracks


def delete_music(paths: ProjectPaths, filename: str) -> None:
    target = safe_join(paths.music, filename)
    if not target.is_file():
        raise ValidationError(
            ErrorCode.MISSING_IMAGE,
            f"'{filename}' parçası bu projede yok.",
        )
    target.unlink()
    logger.info("deleted music %s from %s", filename, paths.root.name)


def _unique_filename(directory: Path, filename: str) -> str:
    """Avoid clobbering an existing upload with the same name."""
    if not (directory / filename).exists():
        return filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 2
    while (directory / f"{stem}-{counter}{suffix}").exists():
        counter += 1
    return f"{stem}-{counter}{suffix}"


def file_hash(path: Path) -> str:
    """Content hash used for cache keys."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]
