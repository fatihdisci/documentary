"""Helpers for building Shorts fixtures without running a real long render.

A synthetic source plus a hand-built manifest lets most of the Shorts suite run
in milliseconds and without FFmpeg. The end-to-end tests that *do* need FFmpeg
build their source with it and are marked accordingly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.shorts.cues import (
    CUE_SIDECAR_SCHEMA_VERSION,
    CueSidecar,
    CueSidecarRef,
    SidecarCue,
    cue_content_hash,
    sidecar_path_for,
)
from app.shorts.manifest import (
    CLEAN_MASTER_FROM_DEDICATED_PASS,
    MANIFEST_SCHEMA_VERSION,
    ManifestEntry,
    ManifestProfile,
    ManifestSource,
    RenderManifest,
    ShortsSourcePackage,
    manifest_path_for,
    sha256_file,
)
from app.shorts.models import ShortRequest, ShortSegmentRequest

#: Three scenes with a 0.5s dissolve between each, an intro and an outro. The
#: numbers below are what the long pipeline would actually have produced: each
#: section starts a transition-duration before the previous one ends.
DEFAULT_TRANSITION = 0.5


def build_entries(
    *,
    scene_count: int = 4,
    scene_duration: float = 10.0,
    with_intro: bool = True,
    with_outro: bool = True,
    transition: float = DEFAULT_TRANSITION,
) -> tuple[list[ManifestEntry], float]:
    """Lay sections out exactly the way ``build_timeline`` does."""
    units: list[tuple[str, str, float]] = []
    if with_intro:
        units.append(("intro", "intro", 6.0))
    for index in range(scene_count):
        units.append((f"scene-{index + 1}", "scene", scene_duration))
    if with_outro:
        units.append(("outro", "outro", 5.0))

    entries: list[ManifestEntry] = []
    cursor = 0.0
    scene_index = 0
    for position, (unit_id, kind, duration) in enumerate(units):
        last = position == len(units) - 1
        outgoing = 0.0 if last else transition
        incoming = 0.0 if position == 0 else transition

        number = 0 if kind == "intro" else (scene_count + 1 if kind == "outro" else scene_index + 1)
        title = (
            "Intro" if kind == "intro"
            else "Outro" if kind == "outro"
            else f"Scene {scene_index + 1}"
        )
        if kind == "scene":
            scene_index += 1

        entries.append(
            ManifestEntry(
                unit_id=unit_id,
                kind=kind,
                number=number,
                title=title,
                start_seconds=round(cursor, 4),
                end_seconds=round(cursor + duration, 4),
                duration_seconds=duration,
                transition_to_next="documentary-dissolve" if outgoing else "none",
                transition_duration_seconds=outgoing,
                transition_from_previous_seconds=incoming,
                safe_start_seconds=round(cursor + incoming, 4),
                safe_end_seconds=round(cursor + duration - outgoing, 4),
            )
        )
        cursor += duration - outgoing

    total = entries[-1].end_seconds
    return entries, total


def make_manifest(
    video: Path,
    *,
    slug: str = "the-dodo",
    render_job_id: str = "render0001",
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    entries: list[ManifestEntry] | None = None,
    total: float | None = None,
    duration_seconds: float | None = None,
    checksum: str | None = None,
    has_audio: bool = True,
    schema_version: int = MANIFEST_SCHEMA_VERSION,
    shorts_source: ShortsSourcePackage | None = None,
    source_has_burned_in_subtitles: bool = True,
) -> RenderManifest:
    if entries is None:
        entries, computed_total = build_entries()
        total = total if total is not None else computed_total
    assert total is not None

    size = video.stat().st_size if video.is_file() else 1_234_567
    return RenderManifest(
        schema_version=schema_version,
        render_job_id=render_job_id,
        project_slug=slug,
        project_snapshot_sha256="0" * 64,
        source=ManifestSource(
            filename=video.name,
            size_bytes=size,
            sha256=checksum or (sha256_file(video) if video.is_file() else "a" * 64),
            width=width,
            height=height,
            duration_seconds=duration_seconds if duration_seconds is not None else total,
            codec="h264",
            pix_fmt="yuv420p",
            avg_frame_rate=f"{fps}/1",
            has_audio=has_audio,
            audio_codec="aac" if has_audio else None,
            audio_sample_rate=48_000 if has_audio else None,
        ),
        profile=ManifestProfile(width=width, height=height, fps=fps, quality="preview"),
        total_duration_seconds=total,
        closing_fade_start_seconds=total,
        entries=entries,
        written_at=datetime.now(timezone.utc),
        source_has_burned_in_subtitles=source_has_burned_in_subtitles,
        shorts_source=shorts_source,
    )


def write_manifest(manifest: RenderManifest, video: Path) -> Path:
    target = manifest_path_for(video)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest.model_dump_json(indent=2), "utf-8")
    return target


# --- Shorts-ready source package -------------------------------------------


def cues_for(entries: list[ManifestEntry], *, per_section: int = 2) -> list[SidecarCue]:
    """Evenly spaced cues inside each section's safe window.

    Deliberately inside the safe range: those are the frames a Short can actually
    cut, so a cue there is one a rebasing test can reason about exactly.
    """
    cues: list[SidecarCue] = []
    index = 1
    for entry in entries:
        span = entry.safe_end_seconds - entry.safe_start_seconds
        if span <= 0:
            continue
        slot = span / per_section
        for position in range(per_section):
            start = entry.safe_start_seconds + position * slot
            cues.append(
                SidecarCue(
                    index=index,
                    unit_id=entry.unit_id,
                    start_seconds=round(start + 0.05, 4),
                    end_seconds=round(start + slot - 0.05, 4),
                    lines=[f"{entry.title} line {position + 1}"],
                )
            )
            index += 1
    return cues


def make_shorts_source(
    clean_master: Path,
    *,
    manifest: RenderManifest,
    cues: list[SidecarCue] | None = None,
    render_job_id: str | None = None,
    origin: str = CLEAN_MASTER_FROM_DEDICATED_PASS,
) -> tuple[ShortsSourcePackage, CueSidecar]:
    """Build the package and side-car a Shorts-native render would have written.

    The clean master must already exist on disk; its real size and checksum are
    recorded, so verification behaves exactly as it does in production.
    """
    sidecar_cues = cues if cues is not None else cues_for(manifest.entries)
    checksum = sha256_file(clean_master)

    sidecar = CueSidecar(
        project_slug=manifest.project_slug,
        render_job_id=render_job_id or manifest.render_job_id,
        clean_master_sha256=checksum,
        total_duration_seconds=manifest.total_duration_seconds,
        timing_source="measured-words",
        cues=sidecar_cues,
    )
    sidecar.content_hash = cue_content_hash(sidecar_cues)

    package = ShortsSourcePackage(
        clean_master=ManifestSource(
            filename=clean_master.name,
            size_bytes=clean_master.stat().st_size,
            sha256=checksum,
            width=manifest.source.width,
            height=manifest.source.height,
            duration_seconds=manifest.source.duration_seconds,
            codec="h264",
            pix_fmt="yuv420p",
            avg_frame_rate=manifest.source.avg_frame_rate,
            has_audio=True,
            audio_codec="aac",
            audio_sample_rate=48_000,
        ),
        origin=origin,
        profile=manifest.profile,
        cue_sidecar=CueSidecarRef(
            filename=sidecar_path_for(clean_master).name,
            schema_version=CUE_SIDECAR_SCHEMA_VERSION,
            sha256="",  # filled in by write_shorts_source
            content_hash=sidecar.content_hash,
            cue_count=len(sidecar_cues),
            timing_source="measured-words",
        ),
        render_job_id=render_job_id or manifest.render_job_id,
        project_snapshot_sha256=manifest.project_snapshot_sha256,
        paired_export_sha256=manifest.source.sha256,
    )
    return package, sidecar


def write_shorts_source(
    package: ShortsSourcePackage, sidecar: CueSidecar, clean_master: Path
) -> ShortsSourcePackage:
    """Write the side-car and stamp its real checksum into the package."""
    target = sidecar_path_for(clean_master)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sidecar.model_dump_json(indent=2), "utf-8")
    package.cue_sidecar.sha256 = sha256_file(target)
    return package


def request_for(
    *unit_ids: str,
    render_id: str = "render0001",
    trims: dict[str, tuple[float | None, float | None]] | None = None,
) -> ShortRequest:
    trims = trims or {}
    return ShortRequest(
        source_render_id=render_id,
        segments=[
            ShortSegmentRequest(
                unit_id=unit_id,
                start_seconds=trims.get(unit_id, (None, None))[0],
                end_seconds=trims.get(unit_id, (None, None))[1],
            )
            for unit_id in unit_ids
        ],
    )


def make_source_video(
    path: Path,
    *,
    seconds: float,
    fps: int = 30,
    width: int = 640,
    height: int = 360,
    settings=None,  # noqa: ANN001
) -> Path:
    """A real, small H.264+AAC file with moving picture and audible tone.

    Small enough to cut in a second, real enough that ffprobe, keyframe probing
    and volume detection all behave the way they would on a genuine export.
    """
    import subprocess

    from app.config import get_settings

    active = settings or get_settings()
    ffmpeg = active.require_tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - argument list, never a shell
        [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds:g}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds:g}",
            "-c:v", "libx264", "-crf", "24", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-r", str(fps), "-fps_mode", "cfr",
            "-t", f"{seconds:g}",
            "-movflags", "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path
