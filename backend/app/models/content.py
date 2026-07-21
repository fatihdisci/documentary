"""The importable content-package schema.

This is the format a finished "animal content package" arrives in. It is
deliberately *separate* from the project schema: it carries only authored
content (words, prompts, framing hints), never file paths, timings, or
render settings. Importing it fills in a project without disturbing anything
the user has already configured.

Documented in docs/content-schema.md, with a downloadable example template.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from app.models.base import CamelModel, to_camel
from app.models.enums import AnimationPreset

CONTENT_SCHEMA_VERSION = 1


class ContentBase(CamelModel):
    """Lenient on unknown fields: a package written for a newer version should
    still import what this build understands rather than failing outright."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="ignore",
    )


class ContentScene(ContentBase):
    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=300)
    narration: str = Field(default="", max_length=20_000)
    image_prompt: str = Field(default="", max_length=4_000)
    fact_note: str = Field(default="", max_length=500)

    #: Framing hints. Optional — sensible defaults apply when absent.
    suggested_animation: AnimationPreset = AnimationPreset.AUTO
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0)

    #: Optional overlay timing, in seconds from the start of the scene.
    title_start_seconds: float | None = Field(default=None, ge=0.0, le=600.0)
    title_duration_seconds: float | None = Field(default=None, gt=0.0, le=600.0)
    subtitle_start_seconds: float | None = Field(default=None, ge=0.0, le=600.0)
    subtitle_duration_seconds: float | None = Field(default=None, gt=0.0, le=600.0)

    #: Explicit image filename. When absent, images map by filename order.
    image_file: str | None = None

    @field_validator("narration")
    @classmethod
    def _narration_is_not_whitespace(cls, v: str) -> str:
        return v.strip()


class ContentSection(ContentBase):
    """Intro or outro content."""

    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=300)
    hook_text: str = Field(default="", max_length=400)
    narration: str = Field(default="", max_length=20_000)
    image_prompt: str = Field(default="", max_length=4_000)
    image_file: str | None = None
    use_first_scene_image: bool = False

    @field_validator("narration")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ContentPackage(ContentBase):
    content_schema_version: int = Field(default=CONTENT_SCHEMA_VERSION)

    common_name: str = Field(default="", max_length=200)
    scientific_name: str = Field(default="", max_length=200)

    video_title: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=10_000)
    tags: list[str] = Field(default_factory=list)
    thumbnail_text: str = Field(default="", max_length=200)
    thumbnail_prompt: str = Field(default="", max_length=4_000)

    intro: ContentSection = Field(default_factory=ContentSection)
    scenes: list[ContentScene] = Field(default_factory=list)
    outro: ContentSection = Field(default_factory=ContentSection)

    #: Applied to narration before synthesis, e.g.
    #: {"Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"}
    pronunciation: dict[str, str] = Field(default_factory=dict)

    @field_validator("scenes")
    @classmethod
    def _at_least_one_scene(cls, v: list[ContentScene]) -> list[ContentScene]:
        if not v:
            raise ValueError("a content package must contain at least one scene")
        if len(v) > 200:
            raise ValueError("a content package cannot contain more than 200 scenes")
        return v
