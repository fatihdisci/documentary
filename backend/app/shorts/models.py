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
    COMPOSE = "compose"
    VALIDATE_OUTPUT = "validate-output"
    PUBLISH = "publish"
    CLEANUP = "cleanup"


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
    """Everything that defines a Short. Hashed to make the cache key."""

    source_render_id: str = Field(min_length=1, max_length=128)
    segments: list[ShortSegmentRequest] = Field(default_factory=list)
    layout: ShortLayout = Field(default_factory=ShortLayout)


# --- source discovery -------------------------------------------------------


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


# --- outputs ----------------------------------------------------------------


class ShortArtifact(CamelModel):
    kind: str
    filename: str
    size_bytes: int = 0
    url: str = ""


class ShortManifest(CamelModel):
    """Written beside every finished Short, describing exactly what it contains."""

    schema_version: int = 1
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
    message: str = "Queued"

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
