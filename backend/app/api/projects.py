"""Project, scene, media and content endpoints."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from app.config import get_settings
from app.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.content import ContentPackage
from app.models.project import Project, Scene
from app.storage import media
from app.storage.content_import import (
    ImageMapping,
    ImportReport,
    apply_content,
    map_images_to_scenes,
    parse_content_json,
)
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join
from app.storage.repository import ProjectRepository, ProjectSummary

logger = logging.getLogger("evb.api.projects")

router = APIRouter(prefix="/api/projects", tags=["projects"])


def repo() -> ProjectRepository:
    return ProjectRepository(get_settings())


def _paths(slug: str) -> ProjectPaths:
    repository = repo()
    repository.load(slug)  # raises a clear 404 if it does not exist
    return repository.paths_for(slug)


# --- request/response models ------------------------------------------------


class CreateProjectRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    common_name: str = ""
    scientific_name: str = ""


class RenameRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)


class DuplicateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectResponse(CamelModel):
    project: Project
    images: list[media.ImageInfo] = []


class ImportContentRequest(CamelModel):
    content: dict
    replace_scenes: bool = True
    map_images: bool = True


class ImportContentResponse(CamelModel):
    project: Project
    report: ImportReport


class UploadImagesResponse(CamelModel):
    images: list[media.ImageInfo]
    mapping: ImageMapping | None = None


class ReorderRequest(CamelModel):
    scene_ids: list[str]


class AssignImageRequest(CamelModel):
    image_file: str | None = None


# --- project lifecycle ------------------------------------------------------


@router.get("", response_model=list[ProjectSummary])
def list_projects(include_archived: bool = Query(default=True)) -> list[ProjectSummary]:
    return repo().list_projects(include_archived=include_archived)


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(request: CreateProjectRequest) -> ProjectResponse:
    settings = get_settings()
    project = Project(name=request.name)
    project.animal.common_name = request.common_name or request.name
    project.animal.scientific_name = request.scientific_name
    # Seed from the user's configured defaults so a new project matches their setup.
    project.video.fps = settings.mutable.default_fps
    project.video.width = settings.mutable.default_width
    project.video.height = settings.mutable.default_height
    project.style.transition_preset = settings.mutable.default_transition
    project.video.scene_lead_in_seconds = settings.mutable.default_scene_lead_in_seconds
    project.video.scene_tail_seconds = settings.mutable.default_scene_tail_seconds
    project.audio.tts_provider = settings.mutable.tts_provider
    project.audio.voice = settings.mutable.default_voice
    project.export.quality = settings.mutable.default_quality
    project.export.intermediate_codec = settings.mutable.intermediate_codec
    project.export.use_hardware_encoder = settings.mutable.use_hardware_encoder

    created = repo().create(request.name, project=project)
    return ProjectResponse(project=created, images=[])


@router.get("/{slug}", response_model=ProjectResponse)
def get_project(slug: str) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    return ProjectResponse(
        project=project, images=media.list_images(repository.paths_for(slug), slug=slug)
    )


@router.put("/{slug}", response_model=ProjectResponse)
def update_project(slug: str, project: Project) -> ProjectResponse:
    repository = repo()
    existing = repository.load(slug)
    # The slug and id are identity, not editable content.
    project.slug = existing.slug
    project.project_id = existing.project_id
    project.created_at = existing.created_at
    saved = repository.save(project)
    return ProjectResponse(project=saved, images=media.list_images(repository.paths_for(slug), slug=slug))


@router.post("/{slug}/rename", response_model=ProjectResponse)
def rename_project(slug: str, request: RenameRequest) -> ProjectResponse:
    return ProjectResponse(project=repo().rename(slug, request.name))


@router.post("/{slug}/duplicate", response_model=ProjectResponse, status_code=201)
def duplicate_project(slug: str, request: DuplicateRequest) -> ProjectResponse:
    return ProjectResponse(project=repo().duplicate(slug, request.name))


@router.post("/{slug}/archive", status_code=204)
def archive_project(slug: str) -> None:
    repo().archive(slug)


@router.post("/{slug}/unarchive", response_model=ProjectResponse)
def unarchive_project(slug: str) -> ProjectResponse:
    return ProjectResponse(project=repo().unarchive(slug))


@router.delete("/{slug}", status_code=204)
def delete_project(slug: str, confirm: str = Query(default="")) -> None:
    """Permanent deletion. Requires ``?confirm=<slug>`` so it cannot happen by accident."""
    if confirm != slug:
        raise ConflictError(
            ErrorCode.SCHEMA_VALIDATION,
            "Deleting a project is permanent and needs confirmation.",
            details=f"pass ?confirm={slug} to proceed",
            suggestion=f"Repeat the request with ?confirm={slug}, or archive the project instead.",
        )
    repo().delete(slug)


@router.get("/{slug}/backups", response_model=list[str])
def list_backups(slug: str) -> list[str]:
    return repo().list_backups(slug)


@router.post("/{slug}/backups/{backup_name}/restore", response_model=ProjectResponse)
def restore_backup(slug: str, backup_name: str) -> ProjectResponse:
    return ProjectResponse(project=repo().restore_backup(slug, backup_name))


@router.post("/{slug}/clean-derived", response_model=dict)
def clean_derived(slug: str) -> dict:
    """Delete every derived asset. User content is untouched."""
    return {"removed": repo().clean_derived(slug)}


# --- bundles ----------------------------------------------------------------


@router.get("/{slug}/bundle")
def export_bundle(slug: str, include_derived: bool = Query(default=False)) -> FileResponse:
    repository = repo()
    project = repository.load(slug)
    destination = get_settings().temp_dir / f"{slug}-bundle.zip"
    repository.export_bundle(slug, destination, include_derived=include_derived)
    return FileResponse(
        destination, media_type="application/zip", filename=f"{project.slug}-bundle.zip"
    )


@router.post("/import-bundle", response_model=ProjectResponse, status_code=201)
async def import_bundle(file: UploadFile = File(...)) -> ProjectResponse:
    settings = get_settings()
    data = await _read_upload(file, max_mb=settings.mutable.max_upload_mb * 8)
    temp = settings.temp_dir / f"import-{file.filename or 'bundle.zip'}"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(data)
    try:
        return ProjectResponse(project=repo().import_bundle(temp))
    finally:
        temp.unlink(missing_ok=True)


# --- content import ---------------------------------------------------------


@router.get("/content/example")
def content_example() -> JSONResponse:
    """The documented example package, downloadable as a starting template."""
    from app.storage.content_import import load_example_package

    path = load_example_package()
    if not path.is_file():
        raise NotFoundError(
            ErrorCode.PROJECT_NOT_FOUND,
            "The bundled example content package is missing from this installation.",
            details=str(path),
            suggestion="Reinstall, or see docs/content-schema.md for the format.",
        )
    return JSONResponse(content=__import__("json").loads(path.read_text("utf-8")))


@router.post("/{slug}/content", response_model=ImportContentResponse)
def import_content(slug: str, request: ImportContentRequest) -> ImportContentResponse:
    import json as _json

    repository = repo()
    project = repository.load(slug)
    settings = get_settings()
    package = parse_content_json(
        _json.dumps(request.content), max_bytes=settings.mutable.max_json_mb * 1_048_576
    )
    report = apply_content(
        project,
        package,
        paths=repository.paths_for(slug),
        replace_scenes=request.replace_scenes,
        map_images=request.map_images,
    )
    return ImportContentResponse(project=repository.save(project), report=report)


@router.post("/{slug}/content/upload", response_model=ImportContentResponse)
async def import_content_file(
    slug: str,
    file: UploadFile = File(...),
    replace_scenes: bool = Query(default=True),
    map_images: bool = Query(default=True),
) -> ImportContentResponse:
    repository = repo()
    project = repository.load(slug)
    settings = get_settings()
    data = await _read_upload(file, max_mb=settings.mutable.max_json_mb)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            ErrorCode.INVALID_JSON,
            f"'{file.filename}' is not valid UTF-8 text.",
            details=str(exc),
            suggestion="Save the content file with UTF-8 encoding.",
        ) from exc

    package = parse_content_json(text, max_bytes=settings.mutable.max_json_mb * 1_048_576)
    paths = repository.paths_for(slug)
    # Keep the original import alongside the project as user content.
    paths.content.mkdir(parents=True, exist_ok=True)
    (paths.content / (file.filename or "content.json")).write_bytes(data)

    report = apply_content(
        project, package, paths=paths, replace_scenes=replace_scenes, map_images=map_images
    )
    return ImportContentResponse(project=repository.save(project), report=report)


@router.get("/{slug}/content/export", response_model=ContentPackage)
def export_content(slug: str) -> ContentPackage:
    """Export the project's authored content back out as a content package."""
    from app.models.content import ContentScene, ContentSection

    project = repo().load(slug)
    return ContentPackage(
        common_name=project.animal.common_name,
        scientific_name=project.animal.scientific_name,
        video_title=project.metadata.video_title,
        description=project.metadata.description,
        tags=project.metadata.tags,
        thumbnail_text=project.metadata.thumbnail_text,
        thumbnail_prompt=project.metadata.thumbnail_prompt,
        pronunciation=project.pronunciation,
        intro=ContentSection(
            title=project.intro.title,
            subtitle=project.intro.subtitle,
            hook_text=project.intro.hook_text,
            narration=project.intro.narration,
            image_prompt=project.intro.image_prompt,
            image_file=project.intro.image_file,
            use_first_scene_image=project.intro.use_first_scene_image,
        ),
        outro=ContentSection(
            title=project.outro.title,
            subtitle=project.outro.subtitle,
            hook_text=project.outro.hook_text,
            narration=project.outro.narration,
            image_prompt=project.outro.image_prompt,
            image_file=project.outro.image_file,
        ),
        scenes=[
            ContentScene(
                title=scene.title,
                subtitle=scene.subtitle,
                narration=scene.narration,
                image_prompt=scene.image_prompt,
                fact_note=scene.fact_note,
                suggested_animation=scene.animation_preset,
                focus_x=scene.focus_x,
                focus_y=scene.focus_y,
                image_file=scene.image_file,
                title_start_seconds=scene.title_timing.start_seconds,
                title_duration_seconds=scene.title_timing.duration_seconds,
                subtitle_start_seconds=scene.subtitle_timing.start_seconds,
                subtitle_duration_seconds=scene.subtitle_timing.duration_seconds,
            )
            for scene in project.scenes
        ],
    )


# --- scenes -----------------------------------------------------------------


@router.post("/{slug}/scenes", response_model=ProjectResponse, status_code=201)
def add_scene(slug: str, scene: Scene | None = None) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    new_scene = scene or Scene()
    new_scene.order = len(project.scenes)
    project.scenes = [*project.scenes, new_scene]
    return ProjectResponse(project=repository.save(project))


@router.put("/{slug}/scenes/{scene_id}", response_model=ProjectResponse)
def update_scene(slug: str, scene_id: str, scene: Scene) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    index = next((i for i, s in enumerate(project.scenes) if s.id == scene_id), None)
    if index is None:
        raise NotFoundError(
            ErrorCode.PROJECT_NOT_FOUND,
            f"Scene '{scene_id}' is not in project '{slug}'.",
            suggestion="Reload the project; the scene may have been deleted in another window.",
        )
    scene.id = scene_id
    scene.order = index
    scenes = list(project.scenes)
    scenes[index] = scene
    project.scenes = scenes
    return ProjectResponse(project=repository.save(project))


@router.post("/{slug}/scenes/{scene_id}/duplicate", response_model=ProjectResponse, status_code=201)
def duplicate_scene(slug: str, scene_id: str) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    source = project.scene_by_id(scene_id)
    if source is None:
        raise NotFoundError(ErrorCode.PROJECT_NOT_FOUND, f"Scene '{scene_id}' is not in project '{slug}'.")
    copy = Scene.model_validate(source.model_dump())
    copy.id = Scene().id
    # A duplicate has no audio of its own yet; generated audio belongs to the original.
    copy.audio_file = None
    copy.audio_hash = None
    copy.audio_duration_seconds = None
    copy.audio_source = source.audio_source.__class__.NONE
    index = project.scenes.index(source)
    scenes = list(project.scenes)
    scenes.insert(index + 1, copy)
    project.scenes = scenes
    return ProjectResponse(project=repository.save(project))


@router.delete("/{slug}/scenes/{scene_id}", response_model=ProjectResponse)
def delete_scene(slug: str, scene_id: str) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    remaining = [s for s in project.scenes if s.id != scene_id]
    if len(remaining) == len(project.scenes):
        raise NotFoundError(ErrorCode.PROJECT_NOT_FOUND, f"Scene '{scene_id}' is not in project '{slug}'.")
    project.scenes = remaining
    return ProjectResponse(project=repository.save(project))


@router.post("/{slug}/scenes/reorder", response_model=ProjectResponse)
def reorder_scenes(slug: str, request: ReorderRequest) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    by_id = {s.id: s for s in project.scenes}

    if set(request.scene_ids) != set(by_id):
        missing = set(by_id) - set(request.scene_ids)
        unknown = set(request.scene_ids) - set(by_id)
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            "The reorder request must list every scene exactly once.",
            details=f"missing: {sorted(missing)}\nunknown: {sorted(unknown)}",
            suggestion="Reload the project and try the reorder again.",
        )

    project.scenes = [by_id[scene_id] for scene_id in request.scene_ids]
    return ProjectResponse(project=repository.save(project))


@router.post("/{slug}/scenes/{scene_id}/image", response_model=ProjectResponse)
def assign_image(slug: str, scene_id: str, request: AssignImageRequest) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    scene = project.scene_by_id(scene_id)
    if scene is None:
        raise NotFoundError(ErrorCode.PROJECT_NOT_FOUND, f"Scene '{scene_id}' is not in project '{slug}'.")

    if request.image_file is not None:
        target = safe_join(repository.paths_for(slug).images, request.image_file)
        if not target.is_file():
            raise ValidationError(
                ErrorCode.MISSING_IMAGE,
                f"Image '{request.image_file}' is not in this project.",
                suggestion="Upload the image first, or choose one that is already there.",
            )
    scene.image_file = request.image_file
    return ProjectResponse(project=repository.save(project))


@router.post("/{slug}/map-images", response_model=ProjectResponse)
def remap_images(slug: str, force: bool = Query(default=False)) -> ProjectResponse:
    repository = repo()
    project = repository.load(slug)
    map_images_to_scenes(project, repository.paths_for(slug), force=force)
    return ProjectResponse(project=repository.save(project))


# --- media ------------------------------------------------------------------


@router.get("/{slug}/images", response_model=list[media.ImageInfo])
def list_images(slug: str) -> list[media.ImageInfo]:
    return media.list_images(_paths(slug), slug=slug)


@router.post("/{slug}/images", response_model=UploadImagesResponse, status_code=201)
async def upload_images(
    slug: str,
    files: list[UploadFile] = File(...),
    auto_map: bool = Query(default=True),
) -> UploadImagesResponse:
    repository = repo()
    project = repository.load(slug)
    paths = repository.paths_for(slug)
    settings = get_settings()

    uploaded: list[media.ImageInfo] = []
    for file in files:
        data = await _read_upload(file, max_mb=settings.mutable.max_upload_mb)
        stored = media.store_image(paths, data, file.filename or "image.png", slug=slug)
        uploaded.append(stored.info)

    mapping: ImageMapping | None = None
    if auto_map and project.scenes:
        mapping = map_images_to_scenes(project, paths)
        repository.save(project)

    return UploadImagesResponse(images=uploaded, mapping=mapping)


@router.delete("/{slug}/images/{filename}", status_code=204)
def delete_image(slug: str, filename: str) -> None:
    repository = repo()
    project = repository.load(slug)
    paths = repository.paths_for(slug)
    media.delete_image(paths, filename)
    # Detach the deleted image from any scene still pointing at it.
    changed = False
    for scene in project.scenes:
        if scene.image_file == filename:
            scene.image_file = None
            changed = True
    if changed:
        repository.save(project)


@router.get("/{slug}/media/images/{filename}")
def serve_image(slug: str, filename: str) -> FileResponse:
    return _serve(_paths(slug).images, filename)


@router.get("/{slug}/media/thumbnails/{filename}")
def serve_thumbnail(slug: str, filename: str) -> FileResponse:
    paths = _paths(slug)
    target = safe_join(paths.thumbnails, filename)
    if not target.is_file():
        # Rebuild lazily: thumbnails are derived and may have been cleaned.
        source_stem = Path(filename).stem
        for candidate in paths.images.glob(f"{source_stem}.*"):
            if candidate.suffix.lower() in media.SUPPORTED_IMAGE_SUFFIXES:
                media.rebuild_thumbnail(paths, candidate.name)
                break
    return _serve(paths.thumbnails, filename)


@router.get("/{slug}/media/audio/{kind}/{filename}")
def serve_audio(slug: str, kind: str, filename: str) -> FileResponse:
    paths = _paths(slug)
    if kind not in {"imported", "generated"}:
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            f"Unknown audio kind '{kind}'.",
            details="expected 'imported' or 'generated'",
        )
    directory = paths.imported_audio if kind == "imported" else paths.generated_audio
    return _serve(directory, filename)


@router.get("/{slug}/media/music/{filename}")
def serve_music(slug: str, filename: str) -> FileResponse:
    return _serve(_paths(slug).music, filename)


@router.post("/{slug}/music", response_model=dict, status_code=201)
async def upload_music(slug: str, file: UploadFile = File(...)) -> dict:
    paths = _paths(slug)
    settings = get_settings()
    data = await _read_upload(file, max_mb=settings.mutable.max_upload_mb * 4)
    stored = media.store_music(paths, data, file.filename or "music.mp3")
    return {"filename": stored.name}


# --- helpers ----------------------------------------------------------------


def _serve(directory: Path, filename: str) -> FileResponse:
    target = safe_join(directory, filename)
    if not target.is_file():
        raise NotFoundError(
            ErrorCode.MISSING_IMAGE,
            f"'{filename}' was not found.",
            details=str(target),
            suggestion="Reload the project; the file may have been deleted or renamed.",
        )
    return FileResponse(target)


async def _read_upload(file: UploadFile, *, max_mb: int) -> bytes:
    """Read an upload, enforcing the size limit without buffering the excess."""
    limit = max_mb * 1_048_576
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > limit:
            raise ValidationError(
                ErrorCode.FILE_TOO_LARGE,
                f"'{file.filename}' exceeds the {max_mb} MB upload limit.",
                suggestion=f"Compress the file, or raise the limit in Settings (currently {max_mb} MB).",
            )
        chunks.append(chunk)
    return b"".join(chunks)
