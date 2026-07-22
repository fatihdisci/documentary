"""Applying a content package to a project, and mapping images onto scenes.

Import is *additive to authored content and conservative about settings*: it
fills in words, prompts and framing hints, and never touches video/style/audio
settings the user has configured.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from app.errors import ErrorCode, ValidationError
from app.models.base import CamelModel
from app.models.content import ContentPackage, ContentScene, ContentSection
from app.models.enums import AnimationPreset
from app.models.project import Project, Scene, Section, TextTiming
from app.storage.layout import ProjectPaths
from app.storage.media import SUPPORTED_IMAGE_SUFFIXES
from app.storage.paths import natural_sort_key

logger = logging.getLogger("evb.content")


class ImportReport(CamelModel):
    """What the import actually did, shown to the user before they commit."""

    scenes_created: int = 0
    scenes_updated: int = 0
    scenes_removed: int = 0
    images_mapped: int = 0
    #: The image assigned to the intro, when it gets its own.
    intro_image: str | None = None
    unmapped_scenes: list[int] = []
    unused_images: list[str] = []
    warnings: list[str] = []


def parse_content_json(text: str, *, max_bytes: int) -> ContentPackage:
    """Parse and validate a content package, with precise error reporting."""
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValidationError(
            ErrorCode.FILE_TOO_LARGE,
            f"The content file is {len(encoded) / 1_048_576:.1f} MB, over the "
            f"{max_bytes / 1_048_576:.0f} MB limit.",
            suggestion="Raise the limit in Settings, or split the package.",
        )

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            ErrorCode.INVALID_JSON,
            f"The content file is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).",
            details=_json_error_context(text, exc.lineno),
            suggestion="Fix the syntax at the position shown above and import again.",
        ) from exc

    if not isinstance(raw, dict):
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            f"The content file must contain a JSON object at the top level, not a {type(raw).__name__}.",
            suggestion="See docs/content-schema.md, or download the example template.",
        )

    try:
        return ContentPackage.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            f"The content package has {exc.error_count()} validation "
            f"{'error' if exc.error_count() == 1 else 'errors'}.",
            details=_format_errors(exc),
            suggestion="Correct the fields listed above. The example template shows the expected shape.",
        ) from exc


def apply_content(
    project: Project,
    package: ContentPackage,
    *,
    paths: ProjectPaths,
    replace_scenes: bool = True,
    map_images: bool = True,
) -> ImportReport:
    """Fill ``project`` in from ``package``. Mutates and returns a report.

    ``replace_scenes=False`` updates existing scenes in place by index, keeping
    any per-scene tuning (audio, motion overrides) the user already did.
    """
    report = ImportReport()

    if package.common_name:
        project.animal.common_name = package.common_name
    if package.scientific_name:
        project.animal.scientific_name = package.scientific_name
    if package.video_title:
        project.metadata.video_title = package.video_title
    if package.description:
        project.metadata.description = package.description
    if package.thumbnail_text:
        project.metadata.thumbnail_text = package.thumbnail_text
    if package.thumbnail_prompt:
        project.metadata.thumbnail_prompt = package.thumbnail_prompt
    if package.tags:
        project.metadata.tags = list(package.tags)
    if package.pronunciation:
        project.pronunciation = {**project.pronunciation, **package.pronunciation}

    _apply_section(project.intro, package.intro)
    _apply_section(project.outro, package.outro)

    existing = list(project.scenes)
    new_scenes: list[Scene] = []

    for index, content_scene in enumerate(package.scenes):
        if replace_scenes or index >= len(existing):
            scene = Scene(order=index)
            report.scenes_created += 1
        else:
            scene = existing[index]
            report.scenes_updated += 1
        _apply_scene(scene, content_scene, index=index)
        new_scenes.append(scene)

    if not replace_scenes and len(existing) > len(package.scenes):
        # Keep scenes the package does not mention rather than deleting work.
        surplus = existing[len(package.scenes) :]
        for offset, scene in enumerate(surplus):
            scene.order = len(new_scenes) + offset
        new_scenes.extend(surplus)
        report.warnings.append(
            f"{len(surplus)} existing scene(s) beyond the package length were kept. "
            "Delete them manually if the package is meant to replace everything."
        )
    elif replace_scenes and existing:
        report.scenes_removed = len(existing)

    project.scenes = new_scenes

    if map_images:
        mapping = map_images_to_scenes(project, paths)
        report.images_mapped = mapping.images_mapped
        report.intro_image = mapping.intro_image
        report.unmapped_scenes = mapping.unmapped_scenes
        report.unused_images = mapping.unused_images
        report.warnings.extend(mapping.warnings)

    # Re-run model validation so scene ordering is normalized.
    project.scenes = list(project.scenes)
    logger.info(
        "applied content package to %s: %d scenes, %d images mapped",
        project.slug,
        len(project.scenes),
        report.images_mapped,
    )
    return report


def _apply_section(section: Section, content: ContentSection) -> None:
    if content.title:
        section.title = content.title
    if content.subtitle:
        section.subtitle = content.subtitle
    if content.hook_text:
        section.hook_text = content.hook_text
    if content.narration:
        section.narration = content.narration
    if content.image_prompt:
        section.image_prompt = content.image_prompt
    if content.image_file:
        section.image_file = content.image_file
    section.use_first_scene_image = content.use_first_scene_image


def _apply_scene(scene: Scene, content: ContentScene, *, index: int) -> None:
    scene.order = index
    scene.title = content.title
    scene.subtitle = content.subtitle
    scene.narration = content.narration
    scene.image_prompt = content.image_prompt
    scene.fact_note = content.fact_note
    scene.focus_x = content.focus_x
    scene.focus_y = content.focus_y

    if content.suggested_animation is not AnimationPreset.AUTO:
        scene.animation_preset = content.suggested_animation
    if content.image_file:
        scene.image_file = content.image_file

    if content.title_start_seconds is not None or content.title_duration_seconds is not None:
        scene.title_timing = TextTiming(
            start_seconds=(
                content.title_start_seconds if content.title_start_seconds is not None else 0.6
            ),
            duration_seconds=(
                content.title_duration_seconds if content.title_duration_seconds is not None else 4.5
            ),
        )
    if content.subtitle_start_seconds is not None or content.subtitle_duration_seconds is not None:
        scene.subtitle_timing = TextTiming(
            start_seconds=(
                content.subtitle_start_seconds if content.subtitle_start_seconds is not None else 1.0
            ),
            duration_seconds=(
                content.subtitle_duration_seconds
                if content.subtitle_duration_seconds is not None
                else 4.0
            ),
        )


class ImageMapping(CamelModel):
    images_mapped: int = 0
    #: The image assigned to the intro, when it gets its own (see below).
    intro_image: str | None = None
    unmapped_scenes: list[int] = []
    unused_images: list[str] = []
    warnings: list[str] = []


def available_images(paths: ProjectPaths) -> list[str]:
    """Project images in natural filename order (01, 02, ..., 10, 11)."""
    if not paths.images.is_dir():
        return []
    names = [
        p.name
        for p in paths.images.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]
    return sorted(names, key=natural_sort_key)


def intro_takes_own_image(project: Project, image_count: int) -> bool:
    """Whether the intro should claim its own image rather than reuse a scene's.

    The intro is a section in its own right, so reusing the first scene's picture
    made the opening and the first scene show the same frame back to back. When
    the project has at least one more image than it has scenes, that surplus image
    becomes the intro's own — the natural "eleven images for ten scenes" layout,
    the first of which is the intro.

    Kept off when there is no surplus (so a ten-image, ten-scene project maps
    exactly as before), when the intro is disabled, and when the intro is
    explicitly set to reuse the first scene's image.
    """
    return (
        project.intro.enabled
        and not project.intro.use_first_scene_image
        and image_count >= len(project.scenes) + 1
    )


def map_images_to_scenes(project: Project, paths: ProjectPaths, *, force: bool = False) -> ImageMapping:
    """Assign images to the intro and scenes in filename order.

    When there is a surplus image (see :func:`intro_takes_own_image`) the first
    image goes to the intro and the rest to the scenes; otherwise every image
    goes to the scenes and the intro reuses the first scene's picture as before.

    Units that already name an image keep it unless ``force`` is set, so a
    re-import does not undo manual remapping.
    """
    result = ImageMapping()
    images = available_images(paths)
    if not images:
        result.warnings.append("No images have been uploaded yet, so no scenes were mapped.")
        result.unmapped_scenes = [s.order for s in project.scenes]
        return result

    # Ordered targets: the intro first (when it takes its own image), then every
    # scene. Filling this list from the sorted images is what makes the first
    # image the intro's and the rest the scenes', in order.
    targets: list[tuple[str, Section | Scene]] = []
    if intro_takes_own_image(project, len(images)):
        targets.append(("intro", project.intro))
    targets.extend(("scene", scene) for scene in project.scenes)

    # A unit's own image is never handed to another unit, even one not being
    # mapped this pass (e.g. the intro when it is not taking its own image).
    assigned = [project.intro.image_file] + [s.image_file for s in project.scenes]
    taken = set() if force else {name for name in assigned if name}
    queue = [name for name in images if name not in taken]
    cursor = 0

    for kind, unit in targets:
        if unit.image_file and not force:
            if unit.image_file not in images:
                result.warnings.append(
                    f"{_target_label(kind, unit)} refers to '{unit.image_file}', which is not in "
                    "this project. Upload it or pick a different image."
                )
            continue
        if cursor < len(queue):
            unit.image_file = queue[cursor]
            cursor += 1
            result.images_mapped += 1
            if kind == "intro":
                result.intro_image = unit.image_file
        elif kind == "scene":
            unit.image_file = None
            result.unmapped_scenes.append(unit.order)  # type: ignore[union-attr]

    result.unused_images = queue[cursor:]
    if result.unmapped_scenes:
        result.warnings.append(
            f"{len(result.unmapped_scenes)} scene(s) have no image. Upload "
            f"{len(result.unmapped_scenes)} more, or remove the extra scenes."
        )
    if result.unused_images:
        result.warnings.append(
            f"{len(result.unused_images)} uploaded image(s) are not used: "
            f"{', '.join(result.unused_images[:5])}"
            + ("…" if len(result.unused_images) > 5 else "")
        )
    return result


def _target_label(kind: str, unit: Section | Scene) -> str:
    if kind == "intro":
        return "The intro"
    return f"Scene {unit.order + 1}"  # type: ignore[union-attr]


def _format_errors(exc: PydanticValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{location}: {error['msg']}")
    return "\n".join(lines)


def _json_error_context(text: str, line_number: int, *, window: int = 2) -> str:
    """Show the offending line and its neighbours, so the fix is obvious."""
    lines = text.splitlines()
    start = max(0, line_number - window - 1)
    end = min(len(lines), line_number + window)
    out = []
    for index in range(start, end):
        marker = ">>" if index == line_number - 1 else "  "
        out.append(f"{marker} {index + 1:4d} | {lines[index]}")
    return "\n".join(out)


def load_example_package() -> Path:
    """Path to the bundled example content package (the Dodo)."""
    return Path(__file__).resolve().parents[2] / "fixtures" / "dodo-content.json"
