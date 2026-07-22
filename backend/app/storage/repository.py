"""Project persistence: create, read, update, duplicate, archive, delete.

Saves are atomic (write to a temp file, fsync, rename) and keep timestamped
backups, so a crash or a bad edit can never leave a project unopenable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from app.config import Settings, get_settings
from app.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.migrations import migrate
from app.models.project import Project
from app.storage.layout import PROJECT_FILE, ProjectPaths
from app.storage.paths import safe_join, slugify

logger = logging.getLogger("evb.repository")

MAX_BACKUPS = 20
ARCHIVE_DIRNAME = "_archived"


class ProjectSummary(CamelModel):
    """Lightweight listing entry — avoids parsing every project in full."""

    slug: str
    project_id: str
    name: str
    common_name: str
    scene_count: int
    updated_at: datetime
    archived: bool = False
    has_images: bool = False
    thumbnail_url: str | None = None


@dataclass
class ProjectRepository:
    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- location -------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.settings.projects_dir

    @property
    def archive_root(self) -> Path:
        return self.root / ARCHIVE_DIRNAME

    def paths_for(self, slug: str, *, archived: bool = False) -> ProjectPaths:
        base = self.archive_root if archived else self.root
        # safe_join rejects a slug containing traversal, even though slugify
        # should already have made that impossible.
        return ProjectPaths(safe_join(base, slug))

    def exists(self, slug: str) -> bool:
        return (self.root / slug / PROJECT_FILE).is_file()

    # --- listing --------------------------------------------------------

    def list_projects(self, *, include_archived: bool = True) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for base, archived in ((self.root, False), (self.archive_root, True)):
            if not base.is_dir() or (archived and not include_archived):
                continue
            for entry in sorted(base.iterdir()):
                if not entry.is_dir() or entry.name == ARCHIVE_DIRNAME:
                    continue
                summary = self._summarize(entry, archived=archived)
                if summary is not None:
                    summaries.append(summary)
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def _summarize(self, directory: Path, *, archived: bool) -> ProjectSummary | None:
        project_file = directory / PROJECT_FILE
        if not project_file.is_file():
            return None
        try:
            raw = json.loads(project_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt project must appear in the list so the user can see and
            # fix it, rather than silently vanishing.
            logger.warning("unreadable project at %s: %s", directory, exc)
            return ProjectSummary(
                slug=directory.name,
                project_id="",
                name=f"{directory.name} (unreadable)",
                common_name="",
                scene_count=0,
                updated_at=datetime.fromtimestamp(project_file.stat().st_mtime, timezone.utc),
                archived=archived,
            )
        scenes = raw.get("scenes") or []
        images_dir = directory / "images"
        thumbs = sorted((directory / "derived" / "thumbnails").glob("*.jpg"))
        return ProjectSummary(
            slug=directory.name,
            project_id=str(raw.get("projectId", "")),
            name=str(raw.get("name", directory.name)),
            common_name=str((raw.get("animal") or {}).get("commonName", "")),
            scene_count=len(scenes) if isinstance(scenes, list) else 0,
            updated_at=_parse_dt(raw.get("updatedAt"), project_file),
            archived=archived,
            has_images=images_dir.is_dir() and any(images_dir.iterdir()),
            thumbnail_url=(
                f"/api/projects/{directory.name}/media/thumbnails/{thumbs[0].name}" if thumbs else None
            ),
        )

    # --- read / write ---------------------------------------------------

    def load(self, slug: str) -> Project:
        paths = self._locate(slug)
        try:
            text = paths.project_file.read_text("utf-8")
        except OSError as exc:
            raise NotFoundError(
                ErrorCode.PROJECT_NOT_FOUND,
                f"'{slug}' projesi okunamadı.",
                details=str(exc),
            ) from exc

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                ErrorCode.INVALID_JSON,
                f"'{slug}' projesinin dosyası bozuk.",
                details=f"{exc}\n\nFile: {paths.project_file}",
                suggestion=(
                    "Projenin backups/ klasöründeki otomatik yedeklerden birini geri yükleyin "
                    "or fix the JSON syntax by hand."
                ),
            ) from exc

        migrated = migrate(raw)
        try:
            return Project.model_validate(migrated)
        except PydanticValidationError as exc:
            raise ValidationError(
                ErrorCode.SCHEMA_VALIDATION,
                f"'{slug}' projesinin dosyası beklenen biçimde değil.",
                details=_format_pydantic_errors(exc),
                suggestion="Projenin backups/ klasöründen bir yedek geri yükleyin ya da listelenen alanları düzeltin.",
            ) from exc

    def save(self, project: Project, *, backup: bool = True) -> Project:
        paths = self.paths_for(project.slug)
        paths.ensure()
        project.touch()

        if backup and paths.project_file.exists():
            self._write_backup(paths)

        payload = project.model_dump_json(indent=2)
        _atomic_write(paths.project_file, payload)
        logger.debug("saved project %s (%d scenes)", project.slug, len(project.scenes))
        return project

    def _write_backup(self, paths: ProjectPaths) -> None:
        paths.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(paths.project_file, paths.backups / f"project-{stamp}.json")
        # Keep the folder from growing without bound.
        backups = sorted(paths.backups.glob("project-*.json"))
        for stale in backups[:-MAX_BACKUPS]:
            stale.unlink(missing_ok=True)

    def list_backups(self, slug: str) -> list[str]:
        paths = self._locate(slug)
        return [p.name for p in sorted(paths.backups.glob("project-*.json"), reverse=True)]

    def restore_backup(self, slug: str, backup_name: str) -> Project:
        paths = self._locate(slug)
        source = safe_join(paths.backups, backup_name)
        if not source.is_file():
            raise NotFoundError(
                ErrorCode.PROJECT_NOT_FOUND,
                f"'{slug}' projesinde '{backup_name}' adlı yedek yok.",
                suggestion="Yedek listesini yenileyin; silinmiş olabilir.",
            )
        # Back up the current state before replacing it, so restore is reversible.
        self._write_backup(paths)
        _atomic_write(paths.project_file, source.read_text("utf-8"))
        return self.load(slug)

    # --- lifecycle ------------------------------------------------------

    def create(self, name: str, *, project: Project | None = None) -> Project:
        slug = self._unique_slug(slugify(name, fallback="project"))
        new_project = project or Project(name=name)
        new_project.name = name
        new_project.slug = slug
        paths = self.paths_for(slug)
        if paths.project_file.exists():
            raise ConflictError(
                ErrorCode.PROJECT_EXISTS,
                f"'{slug}' adında bir proje zaten var.",
            )
        paths.ensure()
        self.save(new_project, backup=False)
        logger.info("created project %s at %s", slug, paths.root)
        return new_project

    def rename(self, slug: str, new_name: str) -> Project:
        """Change the display name. The folder slug is left alone.

        Renaming the folder would break every path already stored in
        project.json and any export in flight, for no user-visible benefit.
        """
        project = self.load(slug)
        project.name = new_name
        return self.save(project)

    def duplicate(self, slug: str, new_name: str) -> Project:
        source = self._locate(slug)
        target_slug = self._unique_slug(slugify(new_name, fallback="project"))
        target = self.paths_for(target_slug)

        shutil.copytree(source.root, target.root)
        # Derived assets and backups are not worth copying; they rebuild.
        for directory in (*target.all_derived(), target.backups, target.exports):
            shutil.rmtree(directory, ignore_errors=True)
        target.ensure()

        project = self.load(target_slug)
        project.name = new_name
        project.slug = target_slug
        project.project_id = Project().project_id  # a copy is a distinct project
        # The copy has no rendered audio yet; force regeneration rather than
        # pointing at files we just deleted.
        for scene in project.scenes:
            if scene.audio_source.value == "generated":
                scene.audio_file = None
                scene.audio_hash = None
        self.save(project, backup=False)
        logger.info("duplicated project %s -> %s", slug, target_slug)
        return project

    def archive(self, slug: str) -> None:
        source = self._locate(slug, archived=False)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        destination = self.archive_root / slug
        if destination.exists():
            destination = self.archive_root / f"{slug}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        shutil.move(str(source.root), str(destination))
        logger.info("archived project %s", slug)

    def unarchive(self, slug: str) -> Project:
        source = self._locate(slug, archived=True)
        destination = self.root / self._unique_slug(slug)
        shutil.move(str(source.root), str(destination))
        project = self.load(destination.name)
        project.slug = destination.name
        return self.save(project, backup=False)

    def delete(self, slug: str) -> None:
        """Permanently remove a project. The API layer requires confirmation."""
        paths = self._locate(slug)
        shutil.rmtree(paths.root)
        logger.warning("deleted project %s permanently", slug)

    def clean_derived(self, slug: str) -> int:
        """Delete every derived asset. User content is never touched."""
        paths = self._locate(slug)
        removed = 0
        for directory in paths.all_derived():
            if not directory.is_dir():
                continue
            for item in directory.rglob("*"):
                if item.is_file():
                    item.unlink()
                    removed += 1
        paths.ensure()
        logger.info("cleaned %d derived files from %s", removed, slug)
        return removed

    # --- bundles --------------------------------------------------------

    def export_bundle(self, slug: str, destination: Path, *, include_derived: bool = False) -> Path:
        paths = self._locate(slug)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(paths.root.rglob("*")):
                if not item.is_file():
                    continue
                relative = item.relative_to(paths.root)
                first = relative.parts[0]
                if not include_derived and first in {"derived", "backups", "exports"}:
                    continue
                if not include_derived and relative.as_posix().startswith("audio/generated/"):
                    continue
                archive.write(item, relative.as_posix())
        logger.info("exported bundle %s -> %s", slug, destination)
        return destination

    def import_bundle(self, archive_path: Path, *, name: str | None = None) -> Project:
        if not zipfile.is_zipfile(archive_path):
            raise ValidationError(
                ErrorCode.INVALID_JSON,
                "Bu dosya bir proje yedeği değil (.zip bekleniyordu).",
                details=str(archive_path),
                suggestion="Bu uygulamadan indirilmiş bir yedek dosyası seçin.",
            )

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if PROJECT_FILE not in names:
                raise ValidationError(
                    ErrorCode.SCHEMA_VALIDATION,
                    "Yedek dosyasının içinde project.json yok.",
                    details=f"entries: {', '.join(names[:20])}",
                    suggestion="Bu uygulamadan indirilmiş bir yedek dosyası seçin.",
                )
            raw = json.loads(archive.read(PROJECT_FILE).decode("utf-8"))
            display_name = name or str(raw.get("name", "Imported project"))
            slug = self._unique_slug(slugify(display_name, fallback="imported-project"))
            target = self.paths_for(slug)
            target.ensure()

            for entry in names:
                # Zip-slip guard: every member must land inside the project root.
                if entry.endswith("/"):
                    continue
                member_path = safe_join(target.root, entry)
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, member_path.open("wb") as sink:
                    shutil.copyfileobj(source, sink)

        project = self.load(slug)
        project.name = display_name
        project.slug = slug
        self.save(project, backup=False)
        logger.info("imported bundle %s as %s", archive_path.name, slug)
        return project

    # --- helpers --------------------------------------------------------

    def _locate(self, slug: str, *, archived: bool | None = None) -> ProjectPaths:
        candidates = (
            [self.paths_for(slug, archived=archived)]
            if archived is not None
            else [self.paths_for(slug), self.paths_for(slug, archived=True)]
        )
        for paths in candidates:
            if paths.project_file.is_file():
                return paths
        raise NotFoundError(
            ErrorCode.PROJECT_NOT_FOUND,
            f"'{slug}' adlı bir proje bulunamadı.",
            details=f"looked in: {', '.join(str(p.root) for p in candidates)}",
        )

    def _unique_slug(self, base: str) -> str:
        candidate = base
        counter = 2
        while (self.root / candidate).exists() or (self.archive_root / candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate


def _atomic_write(target: Path, text: str) -> None:
    """Write via a temp file + fsync + rename so a crash cannot truncate."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(target)


def _parse_dt(value: object, fallback_file: Path) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_file.stat().st_mtime, timezone.utc)


def _format_pydantic_errors(exc: PydanticValidationError) -> str:
    """Render validation errors as a readable, field-by-field list."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{location}: {error['msg']}")
    return "\n".join(lines)
