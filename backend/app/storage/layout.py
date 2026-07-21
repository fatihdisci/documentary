"""Project folder layout.

The central rule of this module: **user content and derived assets live in
different trees.** Anything under ``derived/`` can be deleted at any time and
rebuilt; nothing outside it may ever be written by the render pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_FILE = "project.json"

#: Directories holding files the user gave us. The renderer only ever reads these.
USER_CONTENT_DIRS = ("images", "audio/imported", "music", "content")

#: Everything the renderer produces. Safe to delete; rebuilt on demand.
DERIVED_DIRS = (
    "derived/normalized",
    "derived/thumbnails",
    "derived/cards",
    "derived/subtitles",
    "derived/clips",
    "derived/proxy",
    "audio/generated",
)

OTHER_DIRS = ("exports", "backups", "logs")


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved paths for one project. Constructed from the project root."""

    root: Path

    @property
    def project_file(self) -> Path:
        return self.root / PROJECT_FILE

    # --- user content (read-only to the renderer) ---
    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def imported_audio(self) -> Path:
        return self.root / "audio" / "imported"

    @property
    def music(self) -> Path:
        return self.root / "music"

    @property
    def content(self) -> Path:
        return self.root / "content"

    # --- derived (disposable) ---
    @property
    def generated_audio(self) -> Path:
        return self.root / "audio" / "generated"

    @property
    def normalized(self) -> Path:
        return self.root / "derived" / "normalized"

    @property
    def thumbnails(self) -> Path:
        return self.root / "derived" / "thumbnails"

    @property
    def cards(self) -> Path:
        return self.root / "derived" / "cards"

    @property
    def subtitle_assets(self) -> Path:
        return self.root / "derived" / "subtitles"

    @property
    def clips(self) -> Path:
        return self.root / "derived" / "clips"

    @property
    def proxy(self) -> Path:
        return self.root / "derived" / "proxy"

    @property
    def derived_root(self) -> Path:
        return self.root / "derived"

    # --- outputs ---
    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> None:
        """Create the full directory tree. Idempotent."""
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in (*USER_CONTENT_DIRS, *DERIVED_DIRS, *OTHER_DIRS):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def all_derived(self) -> list[Path]:
        return [self.root / relative for relative in DERIVED_DIRS]

    def is_user_content(self, path: Path) -> bool:
        """True if ``path`` holds something the user supplied.

        Used as a guard before any destructive operation in the pipeline.
        """
        try:
            relative = path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        parts = relative.as_posix()
        return any(parts == d or parts.startswith(f"{d}/") for d in USER_CONTENT_DIRS) or (
            parts == PROJECT_FILE
        )
