"""On-disk storage for publish drafts, upload history and upload assets.

Everything lives inside the project folder, under ``publishing/``::

    publishing/
    ├── drafts.json          one entry per media file
    ├── history.json         every video that actually reached a platform
    └── assets/
        ├── thumbnails/
        └── captions/

Two rules make this safe to reason about:

* **Every write is atomic.** A crash mid-save leaves the previous file intact,
  never a half-written one — which matters most for ``history.json``, the record
  that stops a video being uploaded twice.
* **No path ever comes from the client.** Assets are stored under a sanitized,
  content-derived name and read back through ``safe_join``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.errors import ErrorCode, NotFoundError
from app.publishing.models import PublishDraft, PublishHistoryEntry
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join, sanitize_filename

logger = logging.getLogger("evb.publishing.repository")

DRAFTS_FILE = "drafts.json"
HISTORY_FILE = "history.json"

#: Keep the history bounded; it is a user-facing list, not an audit log.
MAX_HISTORY_ENTRIES = 500


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(target)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)


class PublishingRepository:
    """Reads and writes one project's publishing state."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    # --- drafts -----------------------------------------------------------

    @property
    def drafts_file(self) -> Path:
        return self.paths.publishing / DRAFTS_FILE

    def load_drafts(self) -> dict[str, PublishDraft]:
        """Every stored draft, keyed by media id. Unreadable entries are skipped."""
        if not self.drafts_file.is_file():
            return {}
        try:
            raw = json.loads(self.drafts_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("publishing drafts.json is unreadable and was ignored: %s", exc)
            return {}
        if not isinstance(raw, dict):
            return {}

        drafts: dict[str, PublishDraft] = {}
        for media_id, payload in raw.items():
            try:
                drafts[str(media_id)] = PublishDraft.model_validate(payload)
            except ValueError as exc:
                logger.info("skipping unreadable draft for %s: %s", media_id, exc)
        return drafts

    def get_draft(self, media_id: str) -> PublishDraft | None:
        return self.load_drafts().get(media_id)

    def save_draft(self, draft: PublishDraft) -> PublishDraft:
        drafts = self.load_drafts()
        draft.updated_at = datetime.now(timezone.utc)
        drafts[draft.media_id] = draft
        payload = {
            media_id: entry.model_dump(mode="json", by_alias=True)
            for media_id, entry in sorted(drafts.items())
        }
        _atomic_write_text(self.drafts_file, json.dumps(payload, indent=2, ensure_ascii=False))
        return draft

    def delete_draft(self, media_id: str) -> bool:
        drafts = self.load_drafts()
        if drafts.pop(media_id, None) is None:
            return False
        payload = {
            key: entry.model_dump(mode="json", by_alias=True)
            for key, entry in sorted(drafts.items())
        }
        _atomic_write_text(self.drafts_file, json.dumps(payload, indent=2, ensure_ascii=False))
        return True

    # --- history ----------------------------------------------------------

    @property
    def history_file(self) -> Path:
        return self.paths.publishing / HISTORY_FILE

    def load_history(self) -> list[PublishHistoryEntry]:
        """Every recorded upload, newest first."""
        if not self.history_file.is_file():
            return []
        try:
            raw = json.loads(self.history_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("publishing history.json is unreadable and was ignored: %s", exc)
            return []
        if not isinstance(raw, list):
            return []

        entries: list[PublishHistoryEntry] = []
        for payload in raw:
            try:
                entries.append(PublishHistoryEntry.model_validate(payload))
            except ValueError as exc:
                logger.info("skipping unreadable history entry: %s", exc)
        entries.sort(key=lambda e: e.uploaded_at, reverse=True)
        return entries

    def _write_history(self, entries: list[PublishHistoryEntry]) -> None:
        entries.sort(key=lambda e: e.uploaded_at, reverse=True)
        payload = [
            entry.model_dump(mode="json", by_alias=True)
            for entry in entries[:MAX_HISTORY_ENTRIES]
        ]
        _atomic_write_text(self.history_file, json.dumps(payload, indent=2, ensure_ascii=False))

    def record_upload(self, entry: PublishHistoryEntry) -> PublishHistoryEntry:
        """Insert or replace one entry. Called the moment a video gets an id."""
        entries = [
            existing
            for existing in self.load_history()
            if not (
                existing.platform is entry.platform
                and existing.video_id
                and existing.video_id == entry.video_id
            )
            and existing.entry_id != entry.entry_id
        ]
        entries.append(entry)
        self._write_history(entries)
        return entry

    def find_upload(self, *, sha256: str, platform: str = "youtube") -> PublishHistoryEntry | None:
        """A previous successful upload of the same *bytes*, never the same title.

        Two videos may legitimately share a title; the same file reaching the
        same platform twice is what deserves a warning.
        """
        if not sha256:
            return None
        return next(
            (
                entry
                for entry in self.load_history()
                if entry.platform.value == platform
                and entry.video_id
                and entry.source.sha256 == sha256
            ),
            None,
        )

    def find_by_media_id(
        self, media_id: str, *, platform: str = "youtube"
    ) -> PublishHistoryEntry | None:
        """The newest upload recorded against one media id. Used for badges only."""
        return next(
            (
                entry
                for entry in self.load_history()
                if entry.platform.value == platform
                and entry.video_id
                and entry.media_id == media_id
            ),
            None,
        )

    def get_entry(self, entry_id: str) -> PublishHistoryEntry:
        entry = next((e for e in self.load_history() if e.entry_id == entry_id), None)
        if entry is None:
            raise NotFoundError(
                ErrorCode.PUBLISHING_JOB_NOT_FOUND,
                f"'{entry_id}' numaralı bir yayın kaydı bulunamadı.",
            )
        return entry

    def entry_for_video(
        self, video_id: str, *, platform: str = "youtube"
    ) -> PublishHistoryEntry | None:
        """One recorded post, by its platform id.

        Scoped by platform because the id spaces are unrelated: nothing stops an
        Instagram media id and a Facebook video id from being the same string,
        and updating the wrong row would silently rewrite someone's history.
        """
        return next(
            (
                entry
                for entry in self.load_history()
                if entry.video_id == video_id and entry.platform.value == platform
            ),
            None,
        )

    # --- assets -----------------------------------------------------------

    def store_thumbnail(self, data: bytes, original_name: str, *, suffix: str) -> str:
        """Save a validated thumbnail. Returns its filename inside the project."""
        name = self._asset_name(original_name, default_stem="thumbnail", suffix=suffix)
        target = safe_join(self.paths.publishing_thumbnails, name)
        _atomic_write_bytes(target, data)
        return name

    def store_caption(self, data: bytes, original_name: str) -> str:
        name = self._asset_name(original_name, default_stem="captions", suffix=".srt")
        target = safe_join(self.paths.publishing_captions, name)
        _atomic_write_bytes(target, data)
        return name

    def thumbnail_path(self, filename: str) -> Path:
        target = safe_join(self.paths.publishing_thumbnails, filename)
        if not target.is_file():
            raise NotFoundError(
                ErrorCode.PUBLISHING_ASSET_INVALID,
                f"'{filename}' kapak görseli artık diskte yok.",
                suggestion="Kapak görselini yeniden seçin.",
            )
        return target

    def caption_path(self, filename: str) -> Path:
        target = safe_join(self.paths.publishing_captions, filename)
        if not target.is_file():
            raise NotFoundError(
                ErrorCode.PUBLISHING_ASSET_INVALID,
                f"'{filename}' altyazı dosyası artık diskte yok.",
                suggestion="Altyazı dosyasını yeniden seçin.",
            )
        return target

    @staticmethod
    def _asset_name(original_name: str, *, default_stem: str, suffix: str) -> str:
        """A safe filename with the suffix the content check actually proved.

        The extension comes from the sniffed content, not the upload's claim, so
        a ``.png`` that is really a JPEG is stored as ``.jpg``.
        """
        safe = sanitize_filename(original_name, default_stem=default_stem, default_suffix=suffix)
        stem = Path(safe).stem or default_stem
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{stem}-{stamp}{suffix}"
