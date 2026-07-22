"""The versioned project schema.

Serialized to ``project.json`` in each project folder. Field names are camelCase
on the wire (matching the frontend and the documented content schema) and
snake_case in Python.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import to_camel

from app.models.enums import (
    AnimationPreset,
    AudioSource,
    DurationMode,
    FitMode,
    IntermediateCodec,
    MusicSource,
    QualityPreset,
    TextAnimation,
    TextPosition,
    TransitionPreset,
    TTSProviderName,
)

SCHEMA_VERSION = 1

#: Zoom beyond this visibly softens even a 4K source once supersampled.
MAX_SCALE = 3.0
MIN_SCALE = 1.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseModel):
    """Common config: camelCase aliases, strict about unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
    )


class Animal(Base):
    common_name: str = Field(default="", max_length=200)
    scientific_name: str = Field(default="", max_length=200)


class Metadata(Base):
    video_title: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=10_000)
    thumbnail_text: str = Field(default="", max_length=200)
    thumbnail_prompt: str = Field(default="", max_length=4_000)
    tags: list[str] = Field(default_factory=list)


class VideoSettings(Base):
    width: int = Field(default=1920, ge=256, le=7680)
    height: int = Field(default=1080, ge=144, le=4320)
    fps: int = Field(default=60, ge=1, le=120)
    target_duration_seconds: float = Field(default=300.0, ge=10.0, le=7200.0)
    duration_mode: DurationMode = DurationMode.AUDIO
    transition_duration_seconds: float = Field(default=0.5, ge=0.0, le=5.0)
    #: Silence held after the last narration word so the video does not cut dead.
    audio_tail_seconds: float = Field(default=2.0, ge=0.0, le=15.0)
    #: Silence before a scene's narration starts, and after it ends.
    scene_lead_in_seconds: float = Field(default=0.35, ge=0.0, le=5.0)
    scene_tail_seconds: float = Field(default=0.65, ge=0.0, le=10.0)
    #: Supersample factor for the Ken Burns working image. 3 keeps zoompan's
    #: integer quantization sub-pixel, which is what removes the 60fps stutter.
    supersample_factor: float = Field(default=3.0, ge=1.5, le=4.0)

    @field_validator("width", "height")
    @classmethod
    def _even_dimensions(cls, v: int) -> int:
        if v % 2 != 0:
            raise ValueError("must be even (H.264 with yuv420p requires even dimensions)")
        return v


class TextStyle(Base):
    """Style for one class of text overlay (title, subtitle, caption...)."""

    font_family: str = Field(default="Inter")
    font_weight: int = Field(default=700, ge=100, le=900)
    size: int = Field(default=64, ge=8, le=300)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    letter_spacing: float = Field(default=0.0, ge=-5.0, le=30.0)
    line_spacing: float = Field(default=1.25, ge=0.6, le=3.0)
    shadow: bool = True
    shadow_blur: int = Field(default=12, ge=0, le=64)
    shadow_offset: int = Field(default=3, ge=0, le=40)
    outline_width: int = Field(default=0, ge=0, le=12)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    box: bool = True
    box_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    box_opacity: float = Field(default=0.45, ge=0.0, le=1.0)
    box_padding_x: int = Field(default=32, ge=0, le=200)
    box_padding_y: int = Field(default=18, ge=0, le=200)
    box_radius: int = Field(default=8, ge=0, le=80)
    animation: TextAnimation = TextAnimation.FADE
    fade_in_seconds: float = Field(default=0.5, ge=0.0, le=5.0)
    fade_out_seconds: float = Field(default=0.5, ge=0.0, le=5.0)
    max_width_ratio: float = Field(default=0.62, ge=0.1, le=1.0)


class SubtitleStyle(TextStyle):
    size: int = Field(default=38, ge=8, le=300)
    font_weight: int = Field(default=500, ge=100, le=900)
    max_width_ratio: float = Field(default=0.8, ge=0.1, le=1.0)
    max_chars_per_line: int = Field(default=42, ge=16, le=90)
    max_lines: int = Field(default=2, ge=1, le=4)
    min_cue_seconds: float = Field(default=1.2, ge=0.3, le=5.0)
    max_cue_seconds: float = Field(default=6.0, ge=1.0, le=15.0)
    #: Upper bound on reading speed; cues are stretched if they exceed it.
    max_chars_per_second: float = Field(default=17.0, ge=5.0, le=40.0)

    @model_validator(mode="after")
    def _cue_bounds(self) -> "SubtitleStyle":
        if self.min_cue_seconds >= self.max_cue_seconds:
            raise ValueError("minCueSeconds must be less than maxCueSeconds")
        return self


class Style(Base):
    font_family: str = Field(default="Inter")
    title: TextStyle = Field(default_factory=lambda: TextStyle(size=64, font_weight=700))
    subtitle: TextStyle = Field(default_factory=lambda: TextStyle(size=36, font_weight=400))
    caption: TextStyle = Field(default_factory=lambda: TextStyle(size=38, font_weight=500))
    subtitles: SubtitleStyle = Field(default_factory=SubtitleStyle)
    text_position: TextPosition = TextPosition.BOTTOM_LEFT
    text_safe_margin: int = Field(default=80, ge=0, le=400)
    #: Dark vignette/scrim drawn under text for readability.
    overlay_opacity: float = Field(default=0.45, ge=0.0, le=1.0)
    transition_preset: TransitionPreset = TransitionPreset.DOCUMENTARY_DISSOLVE
    watermark_text: str = Field(default="", max_length=80)
    watermark_opacity: float = Field(default=0.5, ge=0.0, le=1.0)
    show_scientific_name: bool = True


class AudioSettings(Base):
    tts_provider: TTSProviderName = TTSProviderName.EDGE
    voice: str = Field(default="en-US-AndrewNeural")
    speech_rate: float = Field(default=0.95, ge=0.5, le=2.0)
    speech_pitch: float = Field(default=0.0, ge=-50.0, le=50.0)
    voice_volume_db: float = Field(default=-3.0, ge=-40.0, le=10.0)
    music_volume_db: float = Field(default=-30.0, ge=-60.0, le=0.0)
    duck_music_under_speech: bool = True
    duck_strength: float = Field(default=8.0, ge=1.0, le=20.0)
    music_fade_seconds: float = Field(default=2.5, ge=0.0, le=15.0)
    #: EBU R128 integrated target. -16 LUFS is the practical YouTube default.
    target_lufs: float = Field(default=-16.0, ge=-30.0, le=-8.0)
    normalize_loudness: bool = True
    #: Time-stretching narration degrades it; opt-in only.
    allow_time_stretch: bool = False


class MusicSettings(Base):
    source: MusicSource = MusicSource.NONE
    #: Filename inside the project's ``music/`` folder. User content.
    file: str | None = None
    loop_if_short: bool = True
    intro_level_db: float = Field(default=-22.0, ge=-60.0, le=0.0)
    outro_level_db: float = Field(default=-22.0, ge=-60.0, le=0.0)

    @model_validator(mode="after")
    def _uploaded_needs_file(self) -> "MusicSettings":
        if self.source is MusicSource.UPLOADED and not self.file:
            raise ValueError("music.file is required when music.source is 'uploaded'")
        return self


class SubtitleSettings(Base):
    #: Subtitles are burned into the picture by default so a finished video is
    #: captioned without any extra steps — the guided flow never has to ask.
    #: A separate .srt is always exported too; turn ``burn_in`` off for a clean
    #: image (e.g. when uploading to YouTube, which prefers the sidecar).
    export_srt: bool = True
    export_scene_srt: bool = True
    burn_in: bool = True


class TextTiming(Base):
    """When an overlay appears, relative to the start of its section."""

    start_seconds: float = Field(default=0.6, ge=0.0, le=600.0)
    duration_seconds: float = Field(default=4.0, ge=0.1, le=600.0)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


class Scene(Base):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    order: int = Field(default=0, ge=0)
    enabled: bool = True

    # --- User content: never overwritten by the renderer ---
    image_file: str | None = Field(default=None, description="Filename inside images/")
    image_prompt: str = Field(default="", max_length=4_000)
    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=300)
    narration: str = Field(default="", max_length=20_000)
    fact_note: str = Field(default="", max_length=500)
    subtitle_override: list[str] | None = Field(
        default=None, description="Explicit cue texts; bypasses automatic segmentation."
    )

    # --- Audio ---
    audio_file: str | None = Field(default=None, description="Path relative to project root.")
    audio_source: AudioSource = AudioSource.NONE
    audio_duration_seconds: float | None = Field(default=None, ge=0.0)
    #: Hash of the inputs that produced audio_file; drives cache invalidation.
    audio_hash: str | None = None

    # --- Timing ---
    scene_duration_seconds: float | None = Field(default=None, ge=0.1)
    manual_duration_seconds: float | None = Field(default=None, ge=0.1, le=600.0)

    # --- Motion ---
    animation_preset: AnimationPreset = AnimationPreset.AUTO
    start_scale: float = Field(default=1.0, ge=MIN_SCALE, le=MAX_SCALE)
    end_scale: float = Field(default=1.12, ge=MIN_SCALE, le=MAX_SCALE)
    start_x: float = Field(default=0.5, ge=0.0, le=1.0)
    start_y: float = Field(default=0.5, ge=0.0, le=1.0)
    end_x: float = Field(default=0.5, ge=0.0, le=1.0)
    end_y: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Image framing ---
    fit_mode: FitMode = FitMode.FILL
    rotation: int = Field(default=0)

    # --- Transition into the NEXT section ---
    transition_preset: TransitionPreset | None = Field(
        default=None, description="None means inherit the project default."
    )
    transition_duration_seconds: float | None = Field(default=None, ge=0.0, le=5.0)

    # --- Overlay timing ---
    title_timing: TextTiming = Field(default_factory=lambda: TextTiming(start_seconds=0.6, duration_seconds=4.5))
    subtitle_timing: TextTiming = Field(
        default_factory=lambda: TextTiming(start_seconds=1.0, duration_seconds=4.0)
    )

    @field_validator("rotation")
    @classmethod
    def _rotation_quadrant(cls, v: int) -> int:
        if v % 90 != 0:
            raise ValueError("rotation must be a multiple of 90")
        return v % 360

    @model_validator(mode="after")
    def _motion_limits(self) -> "Scene":
        # A pan at scale 1.0 has nowhere to travel and would expose borders if
        # the offsets differed. Catch it here rather than producing black edges.
        moves = abs(self.start_x - self.end_x) > 1e-6 or abs(self.start_y - self.end_y) > 1e-6
        if moves and min(self.start_scale, self.end_scale) <= 1.0 + 1e-6:
            raise ValueError(
                "a pan requires scale > 1.0 at both ends, otherwise the frame would "
                "move outside the image and expose black borders"
            )
        return self

    def effective_duration(self, *, fallback: float = 5.0) -> float:
        """Resolved duration, preferring manual override then computed value."""
        if self.manual_duration_seconds is not None:
            return self.manual_duration_seconds
        if self.scene_duration_seconds is not None:
            return self.scene_duration_seconds
        return fallback


class Section(Base):
    """Intro / outro. Structurally a scene with a few extra fields."""

    enabled: bool = True
    use_first_scene_image: bool = False
    image_file: str | None = None
    image_prompt: str = Field(default="", max_length=4_000)
    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=300)
    hook_text: str = Field(default="", max_length=400)
    narration: str = Field(default="", max_length=20_000)

    audio_file: str | None = None
    audio_source: AudioSource = AudioSource.NONE
    audio_duration_seconds: float | None = Field(default=None, ge=0.0)
    audio_hash: str | None = None

    scene_duration_seconds: float | None = Field(default=None, ge=0.1)
    manual_duration_seconds: float | None = Field(default=None, ge=0.1, le=600.0)

    animation_preset: AnimationPreset = AnimationPreset.SLOW_ZOOM_IN
    start_scale: float = Field(default=1.0, ge=MIN_SCALE, le=MAX_SCALE)
    end_scale: float = Field(default=1.15, ge=MIN_SCALE, le=MAX_SCALE)
    start_x: float = Field(default=0.5, ge=0.0, le=1.0)
    start_y: float = Field(default=0.5, ge=0.0, le=1.0)
    end_x: float = Field(default=0.5, ge=0.0, le=1.0)
    end_y: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0)

    transition_preset: TransitionPreset | None = None
    transition_duration_seconds: float | None = Field(default=None, ge=0.0, le=5.0)

    #: Intro fades up from black; outro fades down to it.
    fade_from_black_seconds: float = Field(default=1.2, ge=0.0, le=10.0)
    fade_to_black_seconds: float = Field(default=0.0, ge=0.0, le=10.0)
    dark_overlay_opacity: float = Field(default=0.35, ge=0.0, le=1.0)

    title_timing: TextTiming = Field(default_factory=lambda: TextTiming(start_seconds=0.8, duration_seconds=5.0))
    subtitle_timing: TextTiming = Field(
        default_factory=lambda: TextTiming(start_seconds=1.6, duration_seconds=4.0)
    )

    def effective_duration(self, *, fallback: float = 5.0) -> float:
        if self.manual_duration_seconds is not None:
            return self.manual_duration_seconds
        if self.scene_duration_seconds is not None:
            return self.scene_duration_seconds
        return fallback


class ExportSettings(Base):
    quality: QualityPreset = QualityPreset.YOUTUBE_HQ
    intermediate_codec: IntermediateCodec = IntermediateCodec.H264_CRF14_FAST
    use_hardware_encoder: bool = False
    export_narration_audio: bool = True
    export_description: bool = True
    keep_temp_files: bool = False


class Project(Base):
    schema_version: int = Field(default=SCHEMA_VERSION)
    project_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str = Field(default="Untitled Project", min_length=1, max_length=200)
    slug: str = Field(default="untitled-project")

    animal: Animal = Field(default_factory=Animal)
    metadata: Metadata = Field(default_factory=Metadata)
    video: VideoSettings = Field(default_factory=VideoSettings)
    style: Style = Field(default_factory=Style)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

    intro: Section = Field(default_factory=Section)
    scenes: list[Scene] = Field(default_factory=list)
    outro: Section = Field(
        default_factory=lambda: Section(fade_from_black_seconds=0.0, fade_to_black_seconds=1.5)
    )

    #: Spelling hints applied to narration before synthesis, e.g.
    #: {"Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"}
    pronunciation: dict[str, str] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _normalize_scene_order(self) -> "Project":
        # Renumber contiguously from 0 so callers can reorder by list position
        # alone without having to maintain the field by hand.
        for index, scene in enumerate(self.scenes):
            if scene.order != index:
                scene.order = index
        return self

    # --- Convenience accessors used across the render pipeline ---

    @property
    def active_scenes(self) -> list[Scene]:
        return [s for s in self.scenes if s.enabled]

    def scene_by_id(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def transition_for(self, scene: Scene | Section) -> TransitionPreset:
        return scene.transition_preset or self.style.transition_preset

    def transition_duration_for(self, scene: Scene | Section) -> float:
        if scene.transition_duration_seconds is not None:
            return scene.transition_duration_seconds
        return self.video.transition_duration_seconds

    def touch(self) -> None:
        self.updated_at = _now()
