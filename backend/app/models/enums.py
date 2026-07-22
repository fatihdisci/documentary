"""Shared enumerations for the project schema.

Kept in one module so the frontend type generator and the render engine agree on
exactly one spelling of every preset name.
"""

from __future__ import annotations

from enum import Enum


class AnimationPreset(str, Enum):
    """Ken Burns motion presets.

    ``AUTO`` means "let the builder pick", which produces a deterministic,
    non-repeating documentary rotation (see render/kenburns.py).
    """

    AUTO = "auto"
    SLOW_ZOOM_IN = "slow-zoom-in"
    SLOW_ZOOM_OUT = "slow-zoom-out"
    PAN_LEFT_TO_RIGHT = "pan-left-to-right"
    PAN_RIGHT_TO_LEFT = "pan-right-to-left"
    PAN_TOP_TO_BOTTOM = "pan-top-to-bottom"
    PAN_BOTTOM_TO_TOP = "pan-bottom-to-top"
    ZOOM_TO_CENTER = "zoom-to-center"
    ZOOM_TO_LEFT = "zoom-to-left"
    ZOOM_TO_RIGHT = "zoom-to-right"
    ZOOM_TO_FOCUS = "zoom-to-focus"
    GENTLE_DIAGONAL = "gentle-diagonal"
    STATIC = "static"


#: The restrained rotation used when a scene asks for AUTO. Deliberately
#: excludes anything showy; consecutive-duplicate avoidance is applied on top.
AUTO_MOTION_ROTATION: tuple[AnimationPreset, ...] = (
    AnimationPreset.SLOW_ZOOM_IN,
    AnimationPreset.PAN_LEFT_TO_RIGHT,
    AnimationPreset.SLOW_ZOOM_OUT,
    AnimationPreset.PAN_RIGHT_TO_LEFT,
    AnimationPreset.ZOOM_TO_FOCUS,
    AnimationPreset.GENTLE_DIAGONAL,
)


class TransitionPreset(str, Enum):
    """Transitions between adjacent sections."""

    NONE = "none"
    CROSS_DISSOLVE = "cross-dissolve"
    DOCUMENTARY_DISSOLVE = "documentary-dissolve"
    SLOW_CINEMATIC_DISSOLVE = "slow-cinematic-dissolve"
    FADE_THROUGH_BLACK = "fade-through-black"
    FADE_THROUGH_WHITE = "fade-through-white"
    DIP_TO_BLACK = "dip-to-black"
    SUBTLE_ZOOM_DISSOLVE = "subtle-zoom-dissolve"
    HORIZONTAL_SLIDE = "horizontal-slide"
    VERTICAL_SLIDE = "vertical-slide"
    BLUR_DISSOLVE = "blur-dissolve"


#: Transitions the app may choose on its own. Everything else is opt-in only,
#: so an unattended render never produces a slide or a white flash.
RESTRAINED_TRANSITIONS: frozenset[TransitionPreset] = frozenset(
    {
        TransitionPreset.NONE,
        TransitionPreset.CROSS_DISSOLVE,
        TransitionPreset.DOCUMENTARY_DISSOLVE,
        TransitionPreset.FADE_THROUGH_BLACK,
    }
)


class DurationMode(str, Enum):
    AUDIO = "audio"
    TARGET = "target"
    MANUAL = "manual"


class FitMode(str, Enum):
    FILL = "fill"
    FIT = "fit"
    CROP = "crop"


class TextPosition(str, Enum):
    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    MIDDLE_LEFT = "middle-left"
    MIDDLE_CENTER = "middle-center"
    MIDDLE_RIGHT = "middle-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"


class TextAnimation(str, Enum):
    NONE = "none"
    FADE = "fade"
    SLIDE_UP = "slide-up"
    SLIDE_LEFT = "slide-left"


class AudioSource(str, Enum):
    """Where a scene's narration audio came from.

    ``IMPORTED`` audio is user content and is never regenerated or overwritten.
    """

    NONE = "none"
    GENERATED = "generated"
    IMPORTED = "imported"


class TTSProviderName(str, Enum):
    EDGE = "edge"
    IMPORTED = "imported"
    ELEVENLABS = "elevenlabs"


class MusicSource(str, Enum):
    """Explicit three-way choice. There is no implicit music."""

    NONE = "none"
    UPLOADED = "uploaded"
    GENERATED_AMBIENT = "generated-ambient"


class QualityPreset(str, Enum):
    PREVIEW = "preview"
    STANDARD = "standard"
    HIGH = "high"
    YOUTUBE_HQ = "youtube-hq"


class IntermediateCodec(str, Enum):
    """Codec for cached per-scene clips. Benchmarked; user-configurable."""

    H264_CRF12 = "h264-crf12"
    H264_CRF14_FAST = "h264-crf14-fast"
    PRORES_LT = "prores-lt"
    PRORES_422 = "prores-422"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class JobPhase(str, Enum):
    """Ordered render phases; used for progress weighting and log grouping."""

    VALIDATE = "validate"
    VERIFY_SOURCES = "verify-sources"
    GENERATE_TTS = "generate-tts"
    PROBE_AUDIO = "probe-audio"
    COMPUTE_TIMELINE = "compute-timeline"
    BUILD_SUBTITLES = "build-subtitles"
    NORMALIZE_IMAGES = "normalize-images"
    RENDER_TEXT_CARDS = "render-text-cards"
    RENDER_SCENE_CLIPS = "render-scene-clips"
    ASSEMBLE = "assemble"
    MIX_AUDIO = "mix-audio"
    ENCODE = "encode"
    VALIDATE_OUTPUT = "validate-output"
    #: Optional second pass: the subtitle-free clean master and cue data that let
    #: Shorts draw their own large captions. Skipped entirely when the project
    #: opted out, and free when the export has no burned-in subtitles anyway.
    PREPARE_SHORTS_SOURCE = "prepare-shorts-source"
    WRITE_ARTIFACTS = "write-artifacts"
    CLEANUP = "cleanup"
