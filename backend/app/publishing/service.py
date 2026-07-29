"""Everything the publishing API needs that is not a running upload.

Media discovery reads the same sources the rest of the app already trusts:

* long videos come from **render manifests** in ``exports/`` — a manifest is only
  written when a render completed and its output validated, so its presence is
  the proof the file is publishable, and it carries the duration, geometry and
  checksum without re-probing anything;
* Shorts come from the finished **Short manifests** in ``exports/shorts/``.

The English ``.srt`` beside a long render is matched through the render job's
recorded artifacts, never guessed from a filename — a file called
``something.srt`` sitting in the exports folder proves nothing about which video
it belongs to.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.errors import AppError, ConflictError, ErrorCode, NotFoundError, ValidationError
from app.models.enums import JobStatus
from app.publishing.models import (
    LOCAL_TIMEZONE,
    MAX_CAPTION_BYTES,
    MAX_DESCRIPTION_BYTES,
    MAX_FACEBOOK_DESCRIPTION_CHARS,
    MAX_INSTAGRAM_CAPTION_CHARS,
    MAX_INSTAGRAM_HASHTAGS,
    MAX_TAGS_LENGTH,
    MAX_THUMBNAIL_BYTES,
    MAX_TIKTOK_TITLE_CHARS,
    MAX_TITLE_CHARS,
    CommonDraft,
    DraftResponse,
    FacebookDraft,
    InstagramDraft,
    MediaItem,
    MediaKind,
    PublishDraft,
    PublishHistoryEntry,
    PublishMode,
    PublishingPlatform,
    SocialDraft,
    SourceFingerprint,
    TikTokDraft,
    YouTubeDraft,
)
from app.publishing.repository import PublishingRepository
from app.shorts.manifest import MANIFEST_SUFFIX, RenderManifest, load_manifest
from app.shorts.models import ShortManifest
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join
from app.storage.repository import ProjectRepository

logger = logging.getLogger("evb.publishing.service")

ISTANBUL = ZoneInfo(LOCAL_TIMEZONE)

#: Media ids are compared and split, never joined onto a path — but they still
#: only ever contain characters that would be safe if they were.
_MEDIA_ID = re.compile(r"^(long|short):([A-Za-z0-9._-]{1,128})$")

#: Content signatures. The extension a browser sends is a claim; this is proof.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: A render made at "preview" quality exists to be checked, not published.
_UNPUBLISHABLE_QUALITIES = {"preview"}


def parse_media_id(media_id: str) -> tuple[MediaKind, str]:
    """Split ``long:<id>`` / ``short:<id>``, rejecting anything else."""
    match = _MEDIA_ID.match(media_id or "")
    if match is None:
        raise ValidationError(
            ErrorCode.PATH_TRAVERSAL,
            f"'{media_id}' geçerli bir dosya kimliği değil.",
            details="kimlikler 'long:<id>' ya da 'short:<id>' biçiminde olmalı",
            http_status=400,
        )
    kind = MediaKind.LONG if match.group(1) == "long" else MediaKind.SHORT
    return kind, match.group(2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tags_length(tags: list[str]) -> int:
    """How long YouTube considers a tag list to be.

    A tag containing a space is quoted on the wire, so it costs two more
    characters than it looks. Tags are comma-separated, hence the separators.
    """
    if not tags:
        return 0
    total = sum(len(tag) + (2 if " " in tag else 0) for tag in tags)
    return total + max(0, len(tags) - 1)


def clean_tags(tags: list[str]) -> list[str]:
    """Trim, drop empties, and remove repeats while keeping the user's order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        value = " ".join(str(tag).split()).strip()
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        cleaned.append(value)
    return cleaned


def resolve_publish_at(local_value: str | None) -> datetime:
    """Bind a local wall-clock string to Europe/Istanbul and demand a future time.

    ``zoneinfo`` resolves the offset for that date, so summer and winter time are
    handled by the database rather than by an assumption baked in here.
    """
    if not local_value or not str(local_value).strip():
        raise ValidationError(
            ErrorCode.YOUTUBE_SCHEDULE_INVALID,
            "Planlı yükleme için bir tarih ve saat seçin.",
            details="publishAtLocal boş",
        )
    text = str(local_value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(
            ErrorCode.YOUTUBE_SCHEDULE_INVALID,
            f"'{text}' bir tarih ve saat olarak okunamadı.",
            details="beklenen biçim: 2026-08-01T22:00",
        ) from exc

    aware = parsed.astimezone(ISTANBUL) if parsed.tzinfo else parsed.replace(tzinfo=ISTANBUL)
    if aware <= datetime.now(ISTANBUL):
        raise ValidationError(
            ErrorCode.YOUTUBE_SCHEDULE_INVALID,
            f"Seçtiğiniz zaman ({aware.strftime('%d.%m.%Y %H:%M')}) geçmişte kaldı.",
            details=f"şu an: {datetime.now(ISTANBUL).strftime('%d.%m.%Y %H:%M')} ({LOCAL_TIMEZONE})",
        )
    return aware


def validate_youtube_metadata(draft: YouTubeDraft) -> list[str]:
    """Check YouTube's limits. Returns warnings; raises for anything blocking."""
    problems: list[str] = []
    title = draft.title.strip()

    if not title:
        problems.append("Başlık boş olamaz.")
    if len(title) > MAX_TITLE_CHARS:
        problems.append(
            f"Başlık {len(title)} karakter; en fazla {MAX_TITLE_CHARS} karakter olabilir."
        )
    if "<" in title or ">" in title:
        problems.append("Başlıkta < ve > karakterleri kullanılamaz.")

    description_bytes = len(draft.description.encode("utf-8"))
    if description_bytes > MAX_DESCRIPTION_BYTES:
        problems.append(
            f"Açıklama {description_bytes} bayt; en fazla {MAX_DESCRIPTION_BYTES} bayt olabilir."
        )
    if "<" in draft.description or ">" in draft.description:
        problems.append("Açıklamada < ve > karakterleri kullanılamaz.")

    length = tags_length(draft.tags)
    if length > MAX_TAGS_LENGTH:
        problems.append(
            f"Etiketlerin toplam uzunluğu {length} karakter; en fazla {MAX_TAGS_LENGTH} olabilir."
        )

    if not draft.category_id.isdigit():
        problems.append("Kategori kodu sayı olmalı.")

    if problems:
        raise ValidationError(
            ErrorCode.YOUTUBE_INVALID_METADATA,
            problems[0] if len(problems) == 1 else "Yayın bilgileri YouTube sınırlarını aşıyor.",
            details="\n".join(problems),
        )

    warnings: list[str] = []
    if len(title) > 70:
        warnings.append(
            "Başlık 70 karakterden uzun; arama sonuçlarında sonu görünmeyebilir."
        )
    return warnings


# --- the other three platforms ----------------------------------------------


def compose_caption(social: SocialDraft) -> str:
    """The text one post actually carries: the caption plus its hashtags.

    Hashtags are kept as a separate field in the editor because they are a list
    with their own rules, and joined here because every one of these platforms
    takes a single string. A tag the user already wrote with a ``#`` is not
    given a second one.
    """
    caption = social.caption.strip()
    tags = [
        tag if tag.startswith("#") else f"#{tag.replace(' ', '')}"
        for tag in clean_tags(list(social.hashtags))
    ]
    if not tags:
        return caption
    return f"{caption}\n\n{' '.join(tags)}".strip()


def validate_instagram_metadata(draft: InstagramDraft, media: MediaItem) -> list[str]:
    """Meta's Reels limits, checked before anything is uploaded anywhere.

    A Reel that is too long or too short is refused by Meta *after* the file has
    been hosted, downloaded and transcoded — several minutes later. Checking the
    numbers the app already knows turns that into an instant, fixable message.
    """
    problems: list[str] = []
    caption = compose_caption(draft)
    if len(caption) > MAX_INSTAGRAM_CAPTION_CHARS:
        problems.append(
            f"Açıklama {len(caption)} karakter; Instagram en fazla "
            f"{MAX_INSTAGRAM_CAPTION_CHARS} karakter kabul eder."
        )
    if len(draft.hashtags) > MAX_INSTAGRAM_HASHTAGS:
        problems.append(
            f"{len(draft.hashtags)} hashtag var; Instagram en fazla "
            f"{MAX_INSTAGRAM_HASHTAGS} tanesini kabul eder."
        )
    problems.extend(_reel_duration_problems(media, minimum=3.0, maximum=15 * 60))

    if problems:
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            problems[0] if len(problems) == 1 else "Instagram bilgileri sınırları aşıyor.",
            details="\n".join(problems),
        )

    warnings: list[str] = []
    if media.width and media.height and media.width > media.height:
        warnings.append(
            "Video yatay. Instagram Reels dikey (9:16) videolar için tasarlanmıştır; yatay bir "
            "video kırpılarak gösterilebilir."
        )
    return warnings


def validate_facebook_metadata(draft: SocialDraft, media: MediaItem) -> list[str]:
    problems: list[str] = []
    description = compose_caption(draft)
    if len(description) > MAX_FACEBOOK_DESCRIPTION_CHARS:
        problems.append(
            f"Açıklama {len(description)} karakter; Facebook en fazla "
            f"{MAX_FACEBOOK_DESCRIPTION_CHARS} karakter kabul eder."
        )
    problems.extend(_reel_duration_problems(media, minimum=3.0, maximum=90 * 60))

    if problems:
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            problems[0] if len(problems) == 1 else "Facebook bilgileri sınırları aşıyor.",
            details="\n".join(problems),
        )

    warnings: list[str] = []
    if media.width and media.height and media.width > media.height:
        warnings.append(
            "Video yatay. Facebook Reels dikey videolar içindir; yatay bir video kırpılabilir."
        )
    return warnings


def validate_tiktok_metadata(
    draft: TikTokDraft, media: MediaItem, *, allowed_privacy: list[str]
) -> list[str]:
    """TikTok's limits, plus the privacy levels this account may actually use.

    ``allowed_privacy`` comes from TikTok's own creator-info query, so an
    unaudited app is stopped here with an explanation rather than at the post
    call with a code.
    """
    problems: list[str] = []
    title = compose_caption(draft)
    if len(title) > MAX_TIKTOK_TITLE_CHARS:
        problems.append(
            f"Başlık {len(title)} karakter; TikTok en fazla {MAX_TIKTOK_TITLE_CHARS} "
            "karakter kabul eder."
        )
    if allowed_privacy and draft.privacy.upper() not in {
        option.upper() for option in allowed_privacy
    }:
        raise ConflictError(
            ErrorCode.TIKTOK_PRIVACY_NOT_ALLOWED,
            f"“{draft.privacy}” gizliliği bu hesap için kullanılamıyor.",
            details="TikTok'un bildirdiği seçenekler: " + ", ".join(allowed_privacy),
        )
    if problems:
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            problems[0],
            details="\n".join(problems),
        )

    warnings: list[str] = []
    if media.duration_seconds and media.duration_seconds > 10 * 60:
        warnings.append(
            "Video 10 dakikadan uzun; TikTok hesabınızın yükleme sınırına takılabilir."
        )
    return warnings


def _reel_duration_problems(media: MediaItem, *, minimum: float, maximum: float) -> list[str]:
    duration = media.duration_seconds or 0.0
    if not duration:
        return []
    if duration < minimum:
        return [f"Video {duration:.1f} saniye; en az {minimum:.0f} saniye olmalı."]
    if duration > maximum:
        return [
            f"Video {duration / 60:.0f} dakika; en fazla {maximum / 60:.0f} dakika olabilir."
        ]
    return []


class PublishingService:
    """Media discovery, drafts, assets and validation for one installation."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.projects = ProjectRepository(self.settings)

    # --- locating ---------------------------------------------------------

    def paths_for(self, slug: str) -> ProjectPaths:
        self.projects.load(slug)  # 404s early if the project is gone
        return self.projects.paths_for(slug)

    def repository(self, slug: str) -> PublishingRepository:
        return PublishingRepository(self.paths_for(slug))

    # --- media ------------------------------------------------------------

    def list_media(self, slug: str, *, include_unrecommended: bool = True) -> list[MediaItem]:
        """Every publishable file that really exists, newest first."""
        project = self.projects.load(slug)
        paths = self.projects.paths_for(slug)
        repository = PublishingRepository(paths)
        drafts = repository.load_drafts()

        items = [
            *self._long_media(slug, project.name, paths),
            *self._short_media(slug, project.name, paths),
        ]
        for item in items:
            item.has_draft = item.media_id in drafts
            published = repository.find_by_media_id(item.media_id)
            item.published_video_id = published.video_id if published else None

        items.sort(key=lambda item: item.created_at, reverse=True)
        if include_unrecommended:
            return items
        return [item for item in items if item.recommended]

    def get_media(self, slug: str, media_id: str) -> MediaItem:
        parse_media_id(media_id)
        item = next(
            (entry for entry in self.list_media(slug) if entry.media_id == media_id), None
        )
        if item is None:
            raise NotFoundError(
                ErrorCode.PUBLISHING_MEDIA_NOT_FOUND,
                "Seçtiğiniz video bu projede bulunamadı.",
                details=f"media id: {media_id}",
            )
        return item

    def media_path(self, slug: str, media_id: str) -> Path:
        """The file on disk. Internal only — never sent to the frontend."""
        kind, _ = parse_media_id(media_id)
        item = self.get_media(slug, media_id)
        paths = self.paths_for(slug)
        directory = paths.exports if kind is MediaKind.LONG else paths.shorts_exports
        target = safe_join(directory, item.filename)
        if not target.is_file():
            raise NotFoundError(
                ErrorCode.PUBLISHING_MEDIA_NOT_FOUND,
                f"'{item.filename}' artık diskte yok.",
                details=f"beklenen konum: {directory}",
            )
        return target

    def _long_media(self, slug: str, project_name: str, paths: ProjectPaths) -> list[MediaItem]:
        if not paths.exports.is_dir():
            return []

        statuses = self._render_job_statuses(slug)
        captions = self._caption_artifacts(slug)
        items: list[MediaItem] = []

        for manifest_path in sorted(paths.exports.glob(f"*{MANIFEST_SUFFIX}")):
            try:
                manifest = load_manifest(manifest_path)
            except AppError as exc:
                logger.info("skipping unusable manifest %s: %s", manifest_path.name, exc)
                continue

            render_id = manifest.render_job_id or manifest_path.name[: -len(MANIFEST_SUFFIX)]
            status = statuses.get(manifest.render_job_id)
            if status is not None and status is not JobStatus.COMPLETED:
                continue

            video = paths.exports / manifest.source.filename
            if not video.is_file():
                continue

            size = video.stat().st_size
            note: str | None = None
            recommended = True
            if manifest.profile.quality in _UNPUBLISHABLE_QUALITIES:
                recommended = False
                note = "Hızlı deneme kalitesinde oluşturulmuş; yayınlamak için uygun değil."
            elif size != manifest.source.size_bytes:
                recommended = False
                note = "Video dosyası oluşturulduğundan beri değişmiş."

            caption = captions.get(manifest.render_job_id)
            if caption is not None and not (paths.exports / caption).is_file():
                caption = None

            created = manifest.written_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            items.append(
                MediaItem(
                    media_id=f"long:{render_id}",
                    kind=MediaKind.LONG,
                    filename=manifest.source.filename,
                    url=f"/api/projects/{slug}/exports/{manifest.source.filename}",
                    project_slug=slug,
                    project_name=project_name,
                    created_at=created,
                    duration_seconds=manifest.total_duration_seconds,
                    size_bytes=size,
                    width=manifest.source.width,
                    height=manifest.source.height,
                    fps=manifest.profile.fps,
                    quality=manifest.profile.quality,
                    thumbnail_url=f"/api/projects/{slug}/shorts/sources/{render_id}/poster",
                    recommended=recommended,
                    note=note,
                    fingerprint=SourceFingerprint(
                        filename=manifest.source.filename,
                        size_bytes=size,
                        sha256=manifest.source.sha256 if size == manifest.source.size_bytes else "",
                    ),
                    caption_filename=caption,
                    caption_url=(
                        f"/api/projects/{slug}/exports/{caption}" if caption else None
                    ),
                )
            )
        return items

    def _short_media(self, slug: str, project_name: str, paths: ProjectPaths) -> list[MediaItem]:
        directory = paths.shorts_exports
        if not directory.is_dir():
            return []

        items: list[MediaItem] = []
        for path in sorted(directory.glob("*.json")):
            try:
                manifest = ShortManifest.model_validate_json(path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                logger.info("skipping unreadable short manifest %s: %s", path.name, exc)
                continue

            video = directory / manifest.filename
            if not video.is_file():
                continue

            size = video.stat().st_size
            note: str | None = None
            if manifest.size_bytes and size != manifest.size_bytes:
                note = "Kısa video dosyası oluşturulduğundan beri değişmiş."

            created = manifest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            items.append(
                MediaItem(
                    media_id=f"short:{manifest.short_id}",
                    kind=MediaKind.SHORT,
                    filename=manifest.filename,
                    url=f"/api/projects/{slug}/shorts/exports/{manifest.filename}",
                    project_slug=slug,
                    project_name=project_name,
                    created_at=created,
                    duration_seconds=manifest.duration_seconds,
                    size_bytes=size,
                    width=manifest.width,
                    height=manifest.height,
                    fps=manifest.fps,
                    quality="short",
                    recommended=note is None,
                    note=note,
                    fingerprint=SourceFingerprint(
                        filename=manifest.filename,
                        size_bytes=size,
                        sha256=manifest.sha256 if size == manifest.size_bytes else "",
                    ),
                )
            )
        return items

    def _render_job_statuses(self, slug: str) -> dict[str, JobStatus]:
        from app.render.jobs import get_job_manager

        try:
            jobs = get_job_manager().list_jobs(project_slug=slug, limit=200)
        except Exception:  # noqa: BLE001 - listing media must not depend on the queue
            return {}
        return {job.id: job.status for job in jobs}

    def _caption_artifacts(self, slug: str) -> dict[str, str]:
        """Job id -> the ``.srt`` that render actually recorded producing.

        Read from the job's artifact list, which the pipeline wrote from the file
        it created. Nothing here pattern-matches a filename, so an unrelated SRT
        in the exports folder can never be attached to the wrong video.
        """
        from app.render.jobs import get_job_manager

        try:
            jobs = get_job_manager().list_jobs(project_slug=slug, limit=200)
        except Exception:  # noqa: BLE001
            return {}

        found: dict[str, str] = {}
        for job in jobs:
            if job.status is not JobStatus.COMPLETED:
                continue
            artifact = next(
                (
                    item
                    for item in job.artifacts
                    if item.kind == "subtitles" and item.filename.lower().endswith(".srt")
                ),
                None,
            )
            if artifact is not None:
                found[job.id] = artifact.filename
        return found

    # --- drafts -----------------------------------------------------------

    def get_draft(self, slug: str, media_id: str) -> DraftResponse:
        """The stored draft, or a fresh one seeded from the project's metadata."""
        media = self.get_media(slug, media_id)
        repository = self.repository(slug)
        stored = repository.get_draft(media_id)

        if stored is None:
            draft = self.seed_draft(slug, media)
        else:
            draft = stored

        return self._draft_response(repository, draft, media, media_id)

    def _draft_response(
        self,
        repository: PublishingRepository,
        draft: PublishDraft,
        media: MediaItem,
        media_id: str,
    ) -> DraftResponse:
        """One draft plus everything the panel renders around it."""
        changed, reason = self._source_changed(draft, media)
        duplicates = {
            platform.value: entry
            for platform in PublishingPlatform
            if (entry := self.find_duplicate(repository, media, media_id, platform)) is not None
        }
        return DraftResponse(
            draft=draft,
            media=media,
            source_changed=changed,
            source_changed_reason=reason,
            duplicate_of=duplicates.get(PublishingPlatform.YOUTUBE.value),
            duplicates=duplicates,
        )

    @staticmethod
    def find_duplicate(
        repository: PublishingRepository,
        media: MediaItem,
        media_id: str,
        platform: PublishingPlatform,
    ) -> PublishHistoryEntry | None:
        """A finished upload of the same bytes to *this* platform.

        Falls back to the media id when the checksum is unknown — which happens
        when the file on disk no longer matches its manifest — so the warning
        does not silently disappear in exactly the case that deserves it most.
        """
        if media.fingerprint.sha256:
            found = repository.find_upload(
                sha256=media.fingerprint.sha256, platform=platform.value
            )
            if found is not None:
                return found
        return repository.find_by_media_id(media_id, platform=platform.value)

    def seed_draft(self, slug: str, media: MediaItem) -> PublishDraft:
        """Build a draft from ``project.metadata`` without saving it.

        The project supplies the starting values and nothing more: editing a
        draft never writes back to ``project.metadata``.
        """
        project = self.projects.load(slug)
        metadata = project.metadata
        tags = clean_tags(list(metadata.tags))
        title = metadata.video_title or project.name

        common = CommonDraft(
            title=title,
            description=metadata.description,
            tags=tags,
            thumbnail_text=metadata.thumbnail_text,
            thumbnail_prompt=metadata.thumbnail_prompt,
        )
        youtube = YouTubeDraft(
            title=title[:MAX_TITLE_CHARS],
            description=metadata.description,
            tags=tags,
        )
        # Shorts almost always carry their captions in the picture, so an SRT
        # would double them up. The file can still be chosen by hand.
        if media.kind is MediaKind.LONG and media.caption_filename:
            youtube.caption_file = media.caption_filename
            youtube.caption_source = "export"
            youtube.upload_captions = True

        # The social platforms take one block of text, so the description is a
        # better starting point than the title alone. The tags become hashtags
        # because that is what they are on those platforms.
        social_caption = f"{title}\n\n{metadata.description}".strip()
        instagram = InstagramDraft(caption=social_caption[:MAX_INSTAGRAM_CAPTION_CHARS], hashtags=tags)
        facebook = FacebookDraft(
            caption=social_caption[:MAX_FACEBOOK_DESCRIPTION_CHARS], hashtags=tags
        )
        tiktok = TikTokDraft(caption=title[:MAX_TIKTOK_TITLE_CHARS], hashtags=tags)

        return PublishDraft(
            media_id=media.media_id,
            project_slug=slug,
            source_fingerprint=media.fingerprint,
            common=common,
            youtube=youtube,
            instagram=instagram,
            facebook=facebook,
            tiktok=tiktok,
        )

    def save_draft(self, slug: str, media_id: str, draft: PublishDraft) -> DraftResponse:
        """Validate and store one draft. The media id in the URL always wins."""
        media = self.get_media(slug, media_id)
        repository = self.repository(slug)

        draft.media_id = media_id
        draft.project_slug = slug
        draft.common.tags = clean_tags(draft.common.tags)
        draft.youtube.tags = clean_tags(draft.youtube.tags)
        draft.instagram.hashtags = clean_tags(draft.instagram.hashtags)
        draft.facebook.hashtags = clean_tags(draft.facebook.hashtags)
        draft.tiktok.hashtags = clean_tags(draft.tiktok.hashtags)

        # A draft is saved constantly while typing, so only structural mistakes
        # block a save. The full YouTube check runs when an upload is requested.
        if len(draft.youtube.title) > MAX_TITLE_CHARS:
            raise ValidationError(
                ErrorCode.YOUTUBE_INVALID_METADATA,
                f"Başlık en fazla {MAX_TITLE_CHARS} karakter olabilir.",
                details=f"gönderilen uzunluk: {len(draft.youtube.title)}",
            )
        if len(draft.youtube.description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
            raise ValidationError(
                ErrorCode.YOUTUBE_INVALID_METADATA,
                f"Açıklama en fazla {MAX_DESCRIPTION_BYTES} bayt olabilir.",
                details=f"gönderilen boyut: {len(draft.youtube.description.encode('utf-8'))} bayt",
            )
        if tags_length(draft.youtube.tags) > MAX_TAGS_LENGTH:
            raise ValidationError(
                ErrorCode.YOUTUBE_INVALID_METADATA,
                f"Etiketlerin toplam uzunluğu en fazla {MAX_TAGS_LENGTH} karakter olabilir.",
                details=f"gönderilen uzunluk: {tags_length(draft.youtube.tags)}",
            )

        # Assets are referenced by name only; prove the names still resolve.
        if draft.youtube.thumbnail_file:
            repository.thumbnail_path(draft.youtube.thumbnail_file)
        if draft.youtube.caption_file:
            self.caption_path(slug, draft.youtube.caption_file, draft.youtube.caption_source)

        # Saving binds the draft to the file that is selected *now*. That is what
        # the "kaynak dosya değişmiş" warning asks the user to do: review the
        # metadata and save it again. Without this the warning would never clear
        # and the file could never be uploaded — the draft would stay bound to a
        # version of the export that no longer exists on disk.
        draft.source_fingerprint = media.fingerprint

        repository.save_draft(draft)
        return self._draft_response(repository, draft, media, media_id)

    def _source_changed(self, draft: PublishDraft, media: MediaItem) -> tuple[bool, str | None]:
        """Whether the file this draft was written for is still that file."""
        recorded = draft.source_fingerprint
        if not recorded.filename:
            return False, None
        if recorded.filename != media.filename:
            return True, (
                f"Taslak '{recorded.filename}' için hazırlanmıştı, seçili dosya "
                f"'{media.filename}'."
            )
        if recorded.size_bytes and recorded.size_bytes != media.size_bytes:
            return True, (
                f"Dosya boyutu değişmiş: taslakta {recorded.size_bytes} bayt, diskte "
                f"{media.size_bytes} bayt."
            )
        if recorded.sha256 and media.fingerprint.sha256 and (
            recorded.sha256 != media.fingerprint.sha256
        ):
            return True, "Dosyanın içeriği taslak hazırlandığından beri değişmiş."
        return False, None

    # --- assets -----------------------------------------------------------

    def store_thumbnail(self, slug: str, data: bytes, original_name: str) -> str:
        """Validate a thumbnail from its *content*, then store it in the project."""
        if not data:
            raise ValidationError(
                ErrorCode.PUBLISHING_ASSET_INVALID,
                "Kapak görseli boş.",
                details="0 bayt yüklendi",
            )
        if len(data) > MAX_THUMBNAIL_BYTES:
            raise ValidationError(
                ErrorCode.FILE_TOO_LARGE,
                f"Kapak görseli {len(data) / 1_048_576:.1f} MB; YouTube en fazla 2 MB kabul eder.",
                details=f"limit: {MAX_THUMBNAIL_BYTES} bayt",
                suggestion="Görseli sıkıştırıp yeniden yükleyin.",
            )

        if data.startswith(_JPEG_MAGIC):
            suffix = ".jpg"
        elif data.startswith(_PNG_MAGIC):
            suffix = ".png"
        else:
            raise ValidationError(
                ErrorCode.PUBLISHING_ASSET_INVALID,
                f"'{original_name}' JPEG ya da PNG değil.",
                details="dosyanın içeriği JPEG/PNG imzasıyla başlamıyor",
                suggestion="Kapak görselini JPEG ya da PNG olarak kaydedip tekrar yükleyin.",
            )

        from PIL import Image, UnidentifiedImageError

        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValidationError(
                ErrorCode.PUBLISHING_ASSET_INVALID,
                f"'{original_name}' bir görsel olarak açılamadı.",
                details=str(exc),
                suggestion="Görseli yeniden kaydedip tekrar yükleyin.",
            ) from exc

        return self.repository(slug).store_thumbnail(data, original_name, suffix=suffix)

    def store_caption(self, slug: str, data: bytes, original_name: str) -> str:
        """Validate an SRT from its content, then store it in the project."""
        validate_srt(data, original_name)
        return self.repository(slug).store_caption(data, original_name)

    def caption_path(self, slug: str, filename: str, source: str) -> Path:
        """Resolve a caption reference to a real file inside the project."""
        paths = self.paths_for(slug)
        if source == "export":
            target = safe_join(paths.exports, filename)
            if not target.is_file():
                raise NotFoundError(
                    ErrorCode.PUBLISHING_ASSET_INVALID,
                    f"'{filename}' altyazı dosyası bu projenin videoları arasında yok.",
                    suggestion="Altyazı dosyasını elle seçin ya da altyazı yüklemeyi kapatın.",
                )
            return target
        return PublishingRepository(paths).caption_path(filename)

    def thumbnail_path(self, slug: str, filename: str) -> Path:
        return self.repository(slug).thumbnail_path(filename)

    # --- history ----------------------------------------------------------

    def history(self, slug: str) -> list[PublishHistoryEntry]:
        return self.repository(slug).load_history()

    # --- upload preparation ----------------------------------------------

    def prepare_upload(
        self,
        slug: str,
        media_id: str,
        *,
        allow_duplicate: bool,
        platform: PublishingPlatform = PublishingPlatform.YOUTUBE,
        allowed_privacy: list[str] | None = None,
    ) -> tuple[MediaItem, PublishDraft, Path, list[str]]:
        """Everything one platform's job needs, validated before a job exists.

        Runs the checks the user can still fix cheaply — metadata limits, the
        schedule, missing assets, a duplicate — so they come back as a 4xx on the
        request rather than as a job that fails a second later.

        Every check that follows is scoped to *one* platform. That is the whole
        point: a file already on YouTube must still be publishable to Instagram,
        and a Reel that Meta rejected must not make the YouTube upload look
        suspect.
        """
        media = self.get_media(slug, media_id)
        repository = self.repository(slug)
        stored = repository.get_draft(media_id)
        if stored is None:
            raise NotFoundError(
                ErrorCode.PUBLISHING_MEDIA_NOT_FOUND,
                "Bu dosya için henüz yayın bilgisi kaydedilmemiş.",
                details=f"media id: {media_id}",
                suggestion="Yayınla panelinde bilgileri doldurup tekrar deneyin.",
            )

        if platform is PublishingPlatform.YOUTUBE:
            warnings = self._prepare_youtube(slug, repository, stored)
        elif platform is PublishingPlatform.INSTAGRAM:
            warnings = validate_instagram_metadata(stored.instagram, media)
        elif platform is PublishingPlatform.FACEBOOK:
            warnings = validate_facebook_metadata(stored.facebook, media)
        else:
            warnings = validate_tiktok_metadata(
                stored.tiktok, media, allowed_privacy=allowed_privacy or []
            )

        video = self.media_path(slug, media_id)
        changed, reason = self._source_changed(stored, media)
        if changed:
            raise ConflictError(
                ErrorCode.PUBLISHING_SOURCE_CHANGED,
                "Seçtiğiniz video, yayın bilgileri hazırlandığından beri değişmiş.",
                details=reason,
            )

        if not allow_duplicate:
            duplicate = self.find_duplicate(repository, media, media_id, platform)
            if duplicate is not None:
                raise ConflictError(
                    ErrorCode.PUBLISHING_DUPLICATE,
                    f"Bu dosya daha önce {platform.label}'a yüklenmiş.",
                    details=(
                        f"{duplicate.title}\n{duplicate.video_url}\n"
                        f"yüklenme: {duplicate.uploaded_at.isoformat()}"
                    ),
                    video_id=duplicate.video_id,
                    video_url=duplicate.video_url,
                )

        return media, stored, video, warnings

    def _prepare_youtube(
        self, slug: str, repository: PublishingRepository, stored: PublishDraft
    ) -> list[str]:
        """The YouTube-only half of ``prepare_upload``, unchanged in behaviour."""
        warnings = validate_youtube_metadata(stored.youtube)
        if stored.youtube.publish_mode is PublishMode.SCHEDULE:
            resolve_publish_at(stored.youtube.publish_at_local)

        if stored.youtube.thumbnail_file:
            repository.thumbnail_path(stored.youtube.thumbnail_file)
        if stored.youtube.upload_captions:
            if not stored.youtube.caption_file:
                raise ValidationError(
                    ErrorCode.PUBLISHING_ASSET_INVALID,
                    "Altyazı yükleme açık ama bir .srt dosyası seçilmemiş.",
                    details="youtube.captionFile boş",
                    suggestion="Bir altyazı dosyası seçin ya da altyazı yüklemeyi kapatın.",
                )
            self.caption_path(
                slug, stored.youtube.caption_file, stored.youtube.caption_source
            )
        return warnings


def validate_srt(data: bytes, original_name: str) -> str:
    """Confirm this really is an SRT, and return its decoded text.

    Deliberately content-based: an empty file, a PDF renamed to ``.srt`` or a
    file with no timing lines would all be accepted by YouTube's uploader and
    then silently produce no captions.
    """
    if not data.strip():
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            f"'{original_name}' boş.",
            details="altyazı dosyasında hiç içerik yok",
        )
    if len(data) > MAX_CAPTION_BYTES:
        raise ValidationError(
            ErrorCode.FILE_TOO_LARGE,
            f"'{original_name}' {len(data) / 1_048_576:.1f} MB; altyazı için fazla büyük.",
            details=f"limit: {MAX_CAPTION_BYTES} bayt",
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            f"'{original_name}' UTF-8 olarak okunamadı.",
            details=str(exc),
            suggestion="Altyazı dosyasını UTF-8 olarak kaydedip tekrar yükleyin.",
        ) from exc

    if "-->" not in text:
        raise ValidationError(
            ErrorCode.PUBLISHING_ASSET_INVALID,
            f"'{original_name}' bir .srt altyazı dosyasına benzemiyor.",
            details="dosyada hiç zaman satırı ('-->') yok",
        )
    return text
