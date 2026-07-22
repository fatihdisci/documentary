"""Wire and storage models for Shorts.

Deliberately separate from ``models/jobs.py``: a Short is a different kind of
work from a long render, and the long ``RenderJob`` schema is load-bearing for
an on-disk history that must keep parsing. Nothing here changes it.

The layout model is written to be extended. Version one ships exactly one
combination — a black canvas with the 16:9 source centred and letterboxed — but
``backgroundStyle`` and ``layoutStyle`` are enums rather than booleans so a
blurred background, a title card or a safe-area overlay can be added later
without a migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import Field

from app.models.base import CamelModel
from app.models.enums import JobStatus

#: The only output geometry version one produces. YouTube treats vertical or
#: square video up to three minutes as a Short.
SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920

#: Duration policy, all in seconds.
MIN_CLIP_SECONDS = 0.5
RECOMMENDED_MIN_SECONDS = 25.0
RECOMMENDED_MAX_SECONDS = 50.0
#: Past this, a Short with an active Content ID claim can be blocked globally.
#: A warning, never a block: the user may hold the licence to their own music.
CONTENT_ID_WARN_SECONDS = 60.0
#: YouTube's hard limit for what counts as a Short.
MAX_SHORT_SECONDS = 180.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ShortBackgroundStyle(str, Enum):
    """How the empty area around the 16:9 source is filled."""

    BLACK = "black"
    #: Reserved. Not selectable in the UI yet.
    BLURRED = "blurred-background"


class ShortLayoutStyle(str, Enum):
    """How the source is placed on the vertical canvas."""

    CENTERED_FIT = "centered-fit"
    #: Reserved. Not selectable in the UI yet.
    TITLE_TOP = "title-top"
    CTA_BOTTOM = "cta-bottom"


class ShortPhase(str, Enum):
    """Ordered phases, used for progress weighting and log grouping."""

    VALIDATE_SOURCE = "validate-source"
    PLAN = "plan"
    CUT_SEGMENTS = "cut-segments"
    CONCAT = "concat"
    #: Only reached in ``shorts-native`` caption mode: draw the caption cards and
    #: pre-composite them into one transparent track.
    BUILD_CAPTIONS = "build-captions"
    COMPOSE = "compose"
    VALIDATE_OUTPUT = "validate-output"
    PUBLISH = "publish"
    CLEANUP = "cleanup"


# --- captions ---------------------------------------------------------------


class ShortCaptionMode(str, Enum):
    """Where a Short's captions come from.

    ``SOURCE_BURNED_IN`` is the historical behaviour and the default for any
    request that does not mention captions: cut the finished, captioned export
    and keep whatever is already in the picture. It works with every render ever
    made, including ones from before this feature existed.

    ``SHORTS_NATIVE`` cuts the render's subtitle-free clean master instead and
    draws large captions on the 1080x1920 canvas. It needs a render that prepared
    a Shorts source package; there is no way to retrofit one, because burned-in
    captions cannot be removed from a finished MP4.

    ``OFF`` also uses the clean master, and draws nothing.
    """

    SOURCE_BURNED_IN = "source-burned-in"
    SHORTS_NATIVE = "shorts-native"
    OFF = "off"

    @property
    def needs_clean_master(self) -> bool:
        return self is not ShortCaptionMode.SOURCE_BURNED_IN


class ShortCaptionPreset(str, Enum):
    """The three sizes the UI offers. Everything else stays internal."""

    STANDARD = "standard"
    LARGE = "large"
    COMPACT = "compact"


class ShortCaptionStyle(CamelModel):
    """Bounded caption design. Every field is a validated value, never free text.

    Nothing here reaches an FFmpeg filtergraph. Text is drawn by Pillow into an
    RGBA PNG exactly like every other overlay in this app; the numbers below only
    decide what that PNG looks like and where it is composited, and the
    compositor sees integers this module computed.

    The defaults are tuned for 1080x1920:

    * bottom-centre, two lines, ~58 px type on a rounded dark box;
    * ``max_width_ratio`` 0.84, so a line breaks well before the canvas edge;
    * ``safe_bottom_inset`` 380 px, which clears the Shorts scrubber, the
      like/comment/share rail and the title-and-channel block that sit over the
      bottom of the frame in the player;
    * the resulting card sits *below* the letterboxed 16:9 picture (which ends
      at y=1264 for a 1920x1080 source), so captions never cover the film.
    """

    preset: ShortCaptionPreset = ShortCaptionPreset.STANDARD

    font_family: str = Field(default="Inter", min_length=1, max_length=80)
    font_weight: int = Field(default=700, ge=100, le=900)
    font_size: int = Field(default=58, ge=20, le=140)
    #: Cap: the fitter shrinks type to make a cue fit ``max_lines``, never below
    #: this fraction of ``font_size``, so captions stay legible on a phone.
    min_font_scale: float = Field(default=0.72, ge=0.4, le=1.0)

    max_width_ratio: float = Field(default=0.84, ge=0.4, le=1.0)
    max_lines: int = Field(default=2, ge=1, le=4)
    safe_bottom_inset: int = Field(default=380, ge=40, le=900)

    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_width: int = Field(default=3, ge=0, le=12)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")

    shadow: bool = True
    shadow_blur: int = Field(default=18, ge=0, le=64)
    shadow_offset: int = Field(default=3, ge=0, le=40)

    box: bool = True
    box_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    box_opacity: float = Field(default=0.62, ge=0.0, le=1.0)
    box_padding_x: int = Field(default=34, ge=0, le=200)
    box_padding_y: int = Field(default=20, ge=0, le=200)
    box_radius: int = Field(default=26, ge=0, le=80)

    line_spacing: float = Field(default=1.22, ge=0.8, le=2.5)
    letter_spacing: float = Field(default=0.0, ge=-5.0, le=30.0)
    #: Cross-fade at each end of a cue. Short on purpose: a caption that fades
    #: slowly reads as lagging the voice.
    fade_seconds: float = Field(default=0.12, ge=0.0, le=1.0)


#: Full styles for each preset. Resolution copies the preset's values and then
#: applies only the fields a request actually set, so sending ``{"preset":
#: "large"}`` gives large type without the client having to restate everything.
CAPTION_PRESETS: dict[ShortCaptionPreset, ShortCaptionStyle] = {
    ShortCaptionPreset.STANDARD: ShortCaptionStyle(preset=ShortCaptionPreset.STANDARD),
    ShortCaptionPreset.LARGE: ShortCaptionStyle(
        preset=ShortCaptionPreset.LARGE,
        font_size=68,
        font_weight=700,
        max_width_ratio=0.86,
        box_padding_x=38,
        box_padding_y=24,
        box_opacity=0.66,
        outline_width=4,
        safe_bottom_inset=360,
    ),
    ShortCaptionPreset.COMPACT: ShortCaptionStyle(
        preset=ShortCaptionPreset.COMPACT,
        font_size=48,
        font_weight=600,
        max_width_ratio=0.82,
        box_padding_x=28,
        box_padding_y=16,
        box_opacity=0.55,
        box_radius=20,
        outline_width=2,
        shadow_blur=14,
        safe_bottom_inset=400,
    ),
}


def resolve_caption_style(style: ShortCaptionStyle | None) -> ShortCaptionStyle:
    """Fill a partial style in from its preset.

    Fields the caller explicitly sent win; everything else comes from the preset.
    That is what keeps the wire contract small — the UI sends a preset name — while
    still allowing a fully specified style for anyone who wants one.
    """
    requested = style if style is not None else ShortCaptionStyle()
    base = CAPTION_PRESETS.get(requested.preset, CAPTION_PRESETS[ShortCaptionPreset.STANDARD])
    overrides = {
        name: getattr(requested, name)
        for name in requested.model_fields_set
        if name != "preset"
    }
    return base.model_copy(update=overrides)


class ShortLayout(CamelModel):
    """Output design. Version one only renders the defaults below."""

    width: int = Field(default=SHORT_WIDTH, ge=256, le=2160)
    height: int = Field(default=SHORT_HEIGHT, ge=256, le=3840)
    background_style: ShortBackgroundStyle = ShortBackgroundStyle.BLACK
    layout_style: ShortLayoutStyle = ShortLayoutStyle.CENTERED_FIT
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    #: Reserved for an opt-in micro-fade between non-contiguous groups. Locked
    #: to zero for now: the user asked for the source's own mixed audio and
    #: picture, with no effect the long render did not already contain.
    group_gap_fade_seconds: float = Field(default=0.0, ge=0.0, le=0.0)


class ShortSegmentRequest(CamelModel):
    """One selected section, in the order the user picked it.

    ``startSeconds``/``endSeconds`` are absolute times in the *source* video.
    Omitting either falls back to that section's safe boundary from the manifest.
    """

    unit_id: str = Field(min_length=1, max_length=128)
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)


class ShortRequest(CamelModel):
    """Everything that defines a Short. Hashed to make the cache key.

    Both caption fields are optional and default to the behaviour that existed
    before them, so a request written against the old contract produces exactly
    the same Short — same pixels, same cache key, same file.
    """

    source_render_id: str = Field(min_length=1, max_length=128)
    segments: list[ShortSegmentRequest] = Field(default_factory=list)
    layout: ShortLayout = Field(default_factory=ShortLayout)
    caption_mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN
    #: ``None`` means "the standard preset". Only consulted in ``shorts-native``.
    caption_style: ShortCaptionStyle | None = None

    def resolved_caption_style(self) -> ShortCaptionStyle:
        return resolve_caption_style(self.caption_style)


# --- source discovery -------------------------------------------------------


class ShortCaptionSupport(CamelModel):
    """Whether one source render can drive Shorts-native captions, and why not.

    The frontend disables the option from this rather than guessing, and shows
    ``reason`` verbatim — it is written to be read by the person who has to act
    on it, not by a developer.
    """

    #: True only when a verified clean master *and* verified cue data both exist.
    native_available: bool = False
    #: Present when ``native_available`` is False. A complete sentence.
    reason: str | None = None
    #: True when the export itself has captions burned into the picture, which is
    #: exactly the thing that cannot be undone.
    source_has_burned_in_subtitles: bool = True
    #: How many captions the clean source carries, when there is one.
    cue_count: int = 0
    clean_master_filename: str | None = None
    cue_sidecar_filename: str | None = None
    cue_schema_version: int | None = None
    #: "primary-export" | "dedicated-pass" | None
    clean_master_origin: str | None = None


class ShortSourceRender(CamelModel):
    """A completed long render that a Short can be cut from."""

    render_id: str
    project_slug: str
    filename: str
    url: str
    created_at: datetime
    duration_seconds: float
    width: int
    height: int
    fps: int
    quality: str
    size_bytes: int
    section_count: int
    has_audio: bool = True
    thumbnail_url: str | None = None
    status: str = JobStatus.COMPLETED.value
    #: False when the file is gone or no longer matches the manifest. The cheap
    #: checks only (existence and size); the checksum is verified at render time.
    usable: bool = True
    issue: str | None = None
    #: Whether this render can drive large Shorts captions. Absent from responses
    #: written before captions existed, hence the permissive default.
    captions: ShortCaptionSupport = Field(default_factory=ShortCaptionSupport)


class ShortTimelineSection(CamelModel):
    """One selectable section card, straight from the manifest."""

    unit_id: str
    kind: str
    number: int
    title: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    safe_start_seconds: float
    safe_end_seconds: float
    safe_duration_seconds: float
    transition_to_next: str
    transition_duration_seconds: float
    transition_from_previous_seconds: float
    fade_in_seconds: float = 0.0


class ShortSourceTimeline(CamelModel):
    source: ShortSourceRender
    fps: int
    total_duration_seconds: float
    sections: list[ShortTimelineSection] = Field(default_factory=list)
    min_clip_seconds: float = MIN_CLIP_SECONDS
    recommended_min_seconds: float = RECOMMENDED_MIN_SECONDS
    recommended_max_seconds: float = RECOMMENDED_MAX_SECONDS
    warn_seconds: float = CONTENT_ID_WARN_SECONDS
    max_seconds: float = MAX_SHORT_SECONDS


# --- the plan ---------------------------------------------------------------


class ShortSegmentPlan(CamelModel):
    """A selected section after trims are resolved and clamped."""

    unit_id: str
    number: int
    title: str
    kind: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    trimmed: bool = False
    group_index: int = 0


class ShortGroupPlan(CamelModel):
    """One contiguous cut taken from the source in a single piece.

    Adjacent selections merge into one group, which is what preserves the
    original transition, subtitle and audio mix between them exactly: the group
    is cut out of the finished video in one span, so nothing is re-assembled.
    """

    index: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    unit_ids: list[str] = Field(default_factory=list)
    numbers: list[int] = Field(default_factory=list)
    #: Transitions that fall inside this group and are therefore preserved.
    preserved_transitions: int = 0


class ShortPlan(CamelModel):
    segments: list[ShortSegmentPlan] = Field(default_factory=list)
    groups: list[ShortGroupPlan] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
    cache_key: str = ""
    warnings: list[str] = Field(default_factory=list)


class ShortPreviewFrame(CamelModel):
    """A real frame lifted from the source at a group's first frame."""

    group_index: int
    time_seconds: float
    url: str


class ShortsPreflightResponse(CamelModel):
    """Everything the user should see before committing to a Short."""

    ready: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: ShortSourceRender | None = None
    plan: ShortPlan | None = None
    total_duration_seconds: float = 0.0
    #: True while the total sits in the recommended band.
    within_recommended_band: bool = False
    exceeds_content_id_warning: bool = False
    exceeds_maximum: bool = False
    recommended_min_seconds: float = RECOMMENDED_MIN_SECONDS
    recommended_max_seconds: float = RECOMMENDED_MAX_SECONDS
    warn_seconds: float = CONTENT_ID_WARN_SECONDS
    max_seconds: float = MAX_SHORT_SECONDS
    preview_frames: list[ShortPreviewFrame] = Field(default_factory=list)
    #: Set when an identical Short already exists, or is already being built.
    cached_short_id: str | None = None
    active_job_id: str | None = None
    estimated_render_seconds: float = 0.0

    #: What this preflight was run for, echoed back so the page never has to
    #: infer it, and the fully resolved style it would use.
    caption_mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN
    caption_style: ShortCaptionStyle | None = None
    caption_support: ShortCaptionSupport = Field(default_factory=ShortCaptionSupport)
    #: How many captions would actually be drawn, after clipping to the cuts.
    caption_cue_count: int = 0


# --- outputs ----------------------------------------------------------------


class ShortArtifact(CamelModel):
    kind: str
    filename: str
    size_bytes: int = 0
    url: str = ""


class ShortCaptionProvenance(CamelModel):
    """A compact record of where a Short's captions came from.

    Deliberately small: enough to answer "why does this Short look like this?"
    from the manifest alone, without needing the render that produced it.
    """

    mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN
    #: Cues in the source, and cues left after clipping to the selected cuts.
    source_cue_count: int = 0
    rendered_cue_count: int = 0
    #: Type size actually drawn, after fitting every cue into ``max_lines``.
    fitted_font_size: int = 0
    font_family: str = ""
    safe_bottom_inset: int = 0
    #: Where the picture came from, and the caption data with it.
    clean_master: str | None = None
    clean_master_sha256: str | None = None
    clean_master_origin: str | None = None
    cue_sidecar: str | None = None
    cue_sidecar_sha256: str | None = None
    cue_content_hash: str | None = None
    cue_schema_version: int | None = None
    cue_timing_source: str | None = None
    #: True when the caption cards were pre-composited into one alpha track
    #: instead of being chained as individual overlays.
    precomposed: bool = False


class ShortManifest(CamelModel):
    """Written beside every finished Short, describing exactly what it contains.

    v2 added the caption fields below. Every one is optional with a default, so a
    v1 manifest from before Shorts captions existed still loads and still lists
    its Short in the history.
    """

    schema_version: int = 2
    short_id: str
    project_slug: str
    filename: str
    cache_key: str
    created_at: datetime = Field(default_factory=_now)
    job_id: str = ""

    source_render_id: str
    source_video: str
    source_sha256: str
    source_manifest_schema_version: int = 1

    layout: ShortLayout = Field(default_factory=ShortLayout)
    width: int = SHORT_WIDTH
    height: int = SHORT_HEIGHT
    fps: int = 60
    duration_seconds: float = 0.0
    size_bytes: int = 0
    sha256: str = ""

    caption_mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN
    #: The fully resolved style, not the partial one the request carried.
    caption_style: ShortCaptionStyle | None = None
    captions: ShortCaptionProvenance | None = None

    plan: ShortPlan = Field(default_factory=ShortPlan)
    request: ShortRequest
    validation: dict = Field(default_factory=dict)


class ShortRecord(CamelModel):
    """A finished Short as the history list shows it."""

    short_id: str
    project_slug: str
    filename: str
    url: str
    created_at: datetime
    duration_seconds: float
    size_bytes: int
    width: int = SHORT_WIDTH
    height: int = SHORT_HEIGHT
    source_render_id: str
    source_video: str
    cache_key: str
    section_numbers: list[int] = Field(default_factory=list)
    section_titles: list[str] = Field(default_factory=list)
    job_id: str = ""
    artifacts: list[ShortArtifact] = Field(default_factory=list)
    caption_mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN
    caption_preset: ShortCaptionPreset | None = None


class ShortJob(CamelModel):
    """One Shorts render. Persisted to disk on every state change."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    project_slug: str
    request: ShortRequest
    cache_key: str = ""
    short_id: str = ""

    status: JobStatus = JobStatus.QUEUED
    phase: ShortPhase = ShortPhase.VALIDATE_SOURCE
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = "Sırada"

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    #: The process that owns this job, so a job killed mid-render is reported as
    #: interrupted rather than sitting in "running" forever.
    pid: int | None = None

    source_render_id: str = ""
    source_video: str = ""
    section_numbers: list[int] = Field(default_factory=list)
    output_file: str | None = None
    artifacts: list[ShortArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: True when the job completed by reusing an identical Short already on disk.
    cache_reused: bool = False

    error_code: str | None = None
    error_message: str | None = None
    error_details: str | None = None
    error_suggestion: str | None = None
    log_file: str | None = None

    total_duration_seconds: float = 0.0
    segment_count: int = 0
    group_count: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or _now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def estimated_remaining_seconds(self) -> float | None:
        if self.status is not JobStatus.RUNNING or self.progress < 0.05:
            return None
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return None
        return max(0.0, elapsed / self.progress - elapsed)


class ShortJobEvent(CamelModel):
    """One server-sent progress update for a Short."""

    job_id: str
    status: JobStatus
    phase: ShortPhase
    progress: float
    message: str
    elapsed_seconds: float
    estimated_remaining_seconds: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_suggestion: str | None = None
