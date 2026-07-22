"""The render manifest: where a Short learns what happened inside an MP4.

A finished long render is a single opaque file. Cutting a section out of it by
guessing from the container duration would be wrong the moment a transition
overlaps two sections — so the pipeline writes this manifest next to the export,
recording the absolute time of every section *as rendered*.

Two properties matter:

* **Immutable and versioned.** The manifest is written once, next to the MP4 it
  describes, and carries a ``schemaVersion``. It is never rewritten in place.
* **Bound to one exact file.** It records the export's size, SHA-256 and ffprobe
  summary. Before any Short is cut, the file on disk is checked against those
  values; a mismatch is a ``stale_render`` error, never a silent bad cut.

``safeStartSeconds``/``safeEndSeconds`` are the payload the Shorts feature
actually cuts on. A transition of duration ``d`` between two sections *overlaps*
them, so the frames in that window belong to both. The safe range excludes the
overlap at each end, which is what stops a non-contiguous selection from
carrying half a dissolve into an unrelated neighbour.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field, ValidationError as PydanticValidationError

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.enums import QualityPreset
from app.models.project import Project, Scene, Section
from app.render.codecs import RenderProfile
from app.timing.probe import probe_video
from app.timing.schedule import Timeline

logger = logging.getLogger("evb.shorts.manifest")

#: Bumped whenever the meaning of a field changes. Readers refuse anything newer.
MANIFEST_SCHEMA_VERSION = 1

MANIFEST_SUFFIX = "-manifest.json"

#: How much the file on disk may differ from the recorded duration before the
#: manifest is considered to describe a different file.
DURATION_MATCH_TOLERANCE = 0.5


class ManifestSource(CamelModel):
    """The exact file this manifest describes."""

    filename: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    duration_seconds: float
    codec: str
    pix_fmt: str
    avg_frame_rate: str
    has_audio: bool
    audio_codec: str | None = None
    audio_sample_rate: int | None = None


class ManifestProfile(CamelModel):
    """Geometry and codecs the long render actually targeted."""

    width: int
    height: int
    fps: int
    quality: str
    video_codec: str = "h264"
    audio_codec: str = "aac"
    audio_sample_rate: int = 48_000


class ManifestEntry(CamelModel):
    """One section, placed on the finished video's absolute timeline."""

    unit_id: str
    kind: str  # "intro" | "scene" | "outro"
    #: What the user sees: intro is 0, active scenes are 1..N, outro is N+1.
    number: int
    title: str

    start_seconds: float
    end_seconds: float
    duration_seconds: float

    transition_to_next: str
    transition_duration_seconds: float
    #: Duration of the transition overlapping the *start* of this section.
    transition_from_previous_seconds: float

    #: The window a Short may cut when this section is taken on its own: the
    #: section minus any transition overlap it shares with a neighbour.
    safe_start_seconds: float
    safe_end_seconds: float

    #: Fade-up from black at the head of this section, if any. Reported so the
    #: UI can warn about a Short that would open on black; not clamped, because
    #: the fade is part of the picture the user rendered.
    fade_in_seconds: float = 0.0

    @property
    def safe_duration_seconds(self) -> float:
        return max(0.0, self.safe_end_seconds - self.safe_start_seconds)


class RenderManifest(CamelModel):
    """Everything a Short needs to know about a finished long render."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    render_job_id: str = ""
    project_slug: str
    #: SHA-256 of the project snapshot this render was produced from.
    project_snapshot_sha256: str
    source: ManifestSource
    profile: ManifestProfile
    total_duration_seconds: float
    #: Absolute time the closing fade-to-black begins, or the total duration if
    #: there is none. Nothing after this is worth putting in a Short.
    closing_fade_start_seconds: float
    entries: list[ManifestEntry] = Field(default_factory=list)
    written_at: datetime

    def entry(self, unit_id: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.unit_id == unit_id), None)


# --- writing ---------------------------------------------------------------


def manifest_path_for(video: Path) -> Path:
    """Where the manifest for ``video`` lives. Always beside the MP4."""
    return video.with_name(f"{video.stem}{MANIFEST_SUFFIX}")


def build_manifest(
    video: Path,
    *,
    project: Project,
    timeline: Timeline,
    profile: RenderProfile,
    quality: QualityPreset,
    checksum: str,
    job_id: str = "",
    settings: Settings | None = None,
) -> RenderManifest:
    """Describe ``video`` and the timeline that produced it."""
    info = probe_video(video, settings=settings or get_settings())
    closing_fade_start = _closing_fade_start(project, timeline)

    entries: list[ManifestEntry] = []
    scene_count = len(timeline.scene_entries)

    for position, entry in enumerate(timeline.entries):
        unit = _unit_for(project, entry.unit_id)
        incoming = (
            timeline.entries[position - 1].transition_duration if position > 0 else 0.0
        )
        outgoing = entry.transition_duration

        safe_start = entry.start_seconds + incoming
        safe_end = entry.end_seconds - outgoing
        if position == len(timeline.entries) - 1:
            # The tail hold and closing fade sit after the last section. Never
            # let a Short end on a frozen frame fading to black.
            safe_end = min(safe_end, closing_fade_start)
        # A pathological transition could invert the window; keep it sane.
        safe_end = max(safe_start, safe_end)

        entries.append(
            ManifestEntry(
                unit_id=entry.unit_id,
                kind=entry.kind,
                number=_display_number(entry.kind, entry.index, scene_count),
                title=_title_for(entry.kind, entry.index, unit),
                start_seconds=round(entry.start_seconds, 4),
                end_seconds=round(entry.end_seconds, 4),
                duration_seconds=round(entry.duration_seconds, 4),
                transition_to_next=entry.transition.value,
                transition_duration_seconds=round(outgoing, 4),
                transition_from_previous_seconds=round(incoming, 4),
                safe_start_seconds=round(safe_start, 4),
                safe_end_seconds=round(safe_end, 4),
                fade_in_seconds=round(
                    getattr(unit, "fade_from_black_seconds", 0.0) or 0.0, 4
                ),
            )
        )

    return RenderManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        render_job_id=job_id,
        project_slug=project.slug,
        project_snapshot_sha256=hashlib.sha256(
            project.model_dump_json().encode("utf-8")
        ).hexdigest(),
        source=ManifestSource(
            filename=video.name,
            size_bytes=video.stat().st_size,
            sha256=checksum or sha256_file(video),
            width=info.width,
            height=info.height,
            duration_seconds=round(info.duration_seconds, 4),
            codec=info.codec,
            pix_fmt=info.pix_fmt,
            avg_frame_rate=info.avg_frame_rate,
            has_audio=info.has_audio,
            audio_codec=info.audio_codec,
            audio_sample_rate=info.audio_sample_rate,
        ),
        profile=ManifestProfile(
            width=profile.width,
            height=profile.height,
            fps=profile.fps,
            quality=quality.value,
        ),
        total_duration_seconds=round(timeline.total_duration_seconds, 4),
        closing_fade_start_seconds=round(closing_fade_start, 4),
        entries=entries,
        written_at=datetime.now(timezone.utc),
    )


def write_render_manifest(
    video: Path,
    *,
    project: Project,
    timeline: Timeline,
    profile: RenderProfile,
    quality: QualityPreset,
    checksum: str,
    job_id: str = "",
    settings: Settings | None = None,
) -> Path:
    """Build and atomically write the manifest for a finished render."""
    manifest = build_manifest(
        video,
        project=project,
        timeline=timeline,
        profile=profile,
        quality=quality,
        checksum=checksum,
        job_id=job_id,
        settings=settings,
    )
    target = manifest_path_for(video)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2), "utf-8")
    tmp.replace(target)
    logger.info("wrote render manifest %s (%d sections)", target.name, len(manifest.entries))
    return target


# --- reading and verifying --------------------------------------------------


def load_manifest(path: Path) -> RenderManifest:
    """Read a manifest, refusing anything unreadable or from a newer schema."""
    if not path.is_file():
        raise NotFoundError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"No render manifest was found next to '{path.name.replace(MANIFEST_SUFFIX, '.mp4')}'.",
            details=f"expected {path}",
        )
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValidationError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"The render manifest '{path.name}' could not be read.",
            details=str(exc),
        ) from exc

    version = raw.get("schemaVersion", raw.get("schema_version"))
    if isinstance(version, int) and version > MANIFEST_SCHEMA_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"The render manifest '{path.name}' was written by a newer version of the app.",
            details=f"manifest schemaVersion={version}, supported={MANIFEST_SCHEMA_VERSION}",
        )

    try:
        return RenderManifest.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"The render manifest '{path.name}' does not match the expected schema.",
            details=str(exc)[:2000],
            suggestion=(
                "Re-render the long video. The new export writes a fresh manifest."
            ),
        ) from exc


def verify_source(
    manifest: RenderManifest,
    video: Path,
    *,
    settings: Settings | None = None,
    check_checksum: bool = True,
) -> None:
    """Confirm the file on disk is still the one the manifest describes.

    Raises ``stale_render`` for anything that would make a cut meaningless: the
    export deleted, re-encoded, truncated, or swapped for a different render.
    """
    if not video.is_file():
        raise NotFoundError(
            ErrorCode.STALE_RENDER,
            f"The exported video '{manifest.source.filename}' is no longer in this "
            "project's exports folder.",
            details=f"expected {video}",
            source_filename=manifest.source.filename,
        )

    size = video.stat().st_size
    if size != manifest.source.size_bytes:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' has changed since it was rendered, so its section "
            "timeline no longer applies.",
            details=(
                f"manifest recorded {manifest.source.size_bytes} bytes, "
                f"the file on disk is {size} bytes"
            ),
            source_filename=video.name,
        )

    if check_checksum:
        digest = sha256_file(video)
        if digest != manifest.source.sha256:
            raise ValidationError(
                ErrorCode.STALE_RENDER,
                f"'{video.name}' no longer matches the render it was produced by.",
                details=(
                    f"manifest sha256 {manifest.source.sha256}\n"
                    f"file     sha256 {digest}"
                ),
                source_filename=video.name,
            )

    try:
        info = probe_video(video, settings=settings or get_settings())
    except AppError as exc:
        # An MP4 that FFmpeg cannot read is stale for our purposes, whatever the
        # underlying reason: truncated, half-written, or not really a video.
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' could not be read by FFmpeg, so no Short can be cut from it.",
            details=exc.details or exc.message,
            suggestion=(
                "The file is corrupt or incomplete. Render the long video again, then "
                "build the Short from the new export."
            ),
            source_filename=video.name,
        ) from exc

    if info.width <= 0 or info.height <= 0:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' has no readable video stream.",
            details=f"ffprobe reported {info.width}x{info.height}",
            source_filename=video.name,
        )
    if not info.has_audio:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' has no audio stream, so a Short cut from it would be silent.",
            details="ffprobe found no audio stream",
            suggestion=(
                "Re-render the long video with narration or music enabled, then build the "
                "Short from that export."
            ),
            source_filename=video.name,
        )
    if abs(info.duration_seconds - manifest.source.duration_seconds) > DURATION_MATCH_TOLERANCE:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' is {info.duration_seconds:.2f}s long but the manifest "
            f"recorded {manifest.source.duration_seconds:.2f}s.",
            details="the file was re-encoded, trimmed or replaced after it was rendered",
            source_filename=video.name,
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- helpers ---------------------------------------------------------------


def _display_number(kind: str, scene_index: int, scene_count: int) -> int:
    if kind == "intro":
        return 0
    if kind == "outro":
        return scene_count + 1
    return scene_index + 1


def _title_for(kind: str, scene_index: int, unit: Scene | Section | None) -> str:
    title = (getattr(unit, "title", "") or "").strip()
    if title:
        return title[:200]
    if kind == "intro":
        return "Intro"
    if kind == "outro":
        return "Outro"
    return f"Scene {scene_index + 1}"


def _unit_for(project: Project, unit_id: str) -> Scene | Section | None:
    if unit_id == "intro":
        return project.intro
    if unit_id == "outro":
        return project.outro
    return project.scene_by_id(unit_id)


def _closing_fade_start(project: Project, timeline: Timeline) -> float:
    """When the final fade-to-black begins, mirroring the assemble stage.

    The pipeline pads the last frame through the audio tail and fades out over
    it. That maths lives in ``RenderPipeline._assemble``; reproducing it here
    keeps the manifest honest about which frames are still real picture.
    """
    total = timeline.total_duration_seconds
    tail = project.video.audio_tail_seconds
    if tail <= 0:
        return total

    last = timeline.entries[-1]
    unit = _unit_for(project, last.unit_id)
    configured = (
        unit.fade_to_black_seconds
        if isinstance(unit, Section) and unit.fade_to_black_seconds > 0
        else min(tail, 1.2)
    )
    fade_length = min(configured, total)
    return max(0.0, total - fade_length)
