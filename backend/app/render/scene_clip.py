"""Per-scene clip rendering (Pass A of the pipeline).

Each scene becomes one silent, cached video clip at the project's full
resolution and frame rate, with Ken Burns motion, the readability scrim, text
overlays and (optionally) burned-in subtitles already composited.

Two design rules from the brief are enforced here:

* **Subtitles are burned during the per-scene render, never during assembly.**
  Assembly therefore only ever sees a handful of inputs regardless of how many
  cues the video has.
* **Overlay count per scene is capped.** Above the cap, the scene's cues are
  pre-composited into a single transparent overlay clip in one extra pass, so a
  dense scene cannot blow up the filtergraph.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from app.config import Settings, get_settings
from app.errors import ErrorCode, ValidationError
from app.models.enums import AnimationPreset, TextPosition
from app.models.project import Project, Scene, Section
from app.render.codecs import RenderProfile, intermediate_spec
from app.render.ffmpeg import FFmpegRunner, base_output_args, progress_args
from app.render.kenburns import build_zoompan_filter, resolve_motion
from app.render.text import TextCard, render_card, render_scrim
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join
from app.timing.schedule import TimelineEntry
from app.timing.subtitles import Cue

logger = logging.getLogger("evb.scene_clip")

#: Above this many subtitle overlays in one scene, the cues are pre-composited
#: into a single overlay clip instead of being chained individually.
MAX_INLINE_SUBTITLE_OVERLAYS = 12

#: Gap between the title and the caption beneath it, in pixels.
CAPTION_GAP = 90

#: Vertical space reserved at the bottom for burned-in subtitles, as a fraction
#: of frame height. Titles are lifted above this band so the two never collide.
SUBTITLE_BAND_RATIO = 0.135


@dataclass
class SceneClip:
    path: Path
    unit_id: str
    duration_seconds: float
    reused: bool
    cache_key: str
    log: list[str] = field(default_factory=list)


def normalize_image(
    source: Path,
    target: Path,
    *,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
) -> Path:
    """Produce a working copy cropped to the output aspect ratio.

    The original is never modified. Cropping is biased toward the focus point so
    the subject survives a 16:9 crop of a portrait or square source.
    """
    if target.is_file():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.load()
        # Honour EXIF rotation, otherwise phone photos come out sideways.
        oriented = ImageOps.exif_transpose(image)
        rgb = oriented.convert("RGB")

        target_ratio = width / height
        source_ratio = rgb.width / rgb.height

        if abs(source_ratio - target_ratio) < 1e-3:
            cropped = rgb
        elif source_ratio > target_ratio:
            # Too wide: trim the sides, keeping the focus point in view.
            new_width = int(round(rgb.height * target_ratio))
            max_left = rgb.width - new_width
            left = int(round(focus_x * rgb.width - new_width / 2))
            cropped = rgb.crop((max(0, min(left, max_left)), 0,
                                max(0, min(left, max_left)) + new_width, rgb.height))
        else:
            # Too tall: trim top and bottom.
            new_height = int(round(rgb.width / target_ratio))
            max_top = rgb.height - new_height
            top = int(round(focus_y * rgb.height - new_height / 2))
            cropped = rgb.crop((0, max(0, min(top, max_top)),
                                rgb.width, max(0, min(top, max_top)) + new_height))

        # Never upscale here: zoompan does its own supersampled scale, and
        # enlarging twice just softens the picture.
        if cropped.width > width * 4:
            cropped = cropped.resize((width * 4, height * 4), Image.LANCZOS)

        cropped.save(target, "PNG")
    return target


def _full_profile(project: Project) -> RenderProfile:
    """The geometry a full-quality render uses: the project's own video settings."""
    return RenderProfile(
        width=project.video.width,
        height=project.video.height,
        fps=project.video.fps,
        supersample=project.video.supersample_factor,
    )


def cache_key(
    project: Project,
    unit: Scene | Section,
    entry: TimelineEntry,
    cues: list[Cue],
    *,
    image_path: Path | None,
    suppress_fade_out: bool = False,
    profile: RenderProfile | None = None,
) -> str:
    """Hash of every input that changes this clip's pixels.

    Deliberately excludes anything that does not: the music, the *final-encode*
    quality, other scenes' content, and the narration audio itself (only its
    measured duration matters here). The render ``profile`` is included because
    preview renders at a lower frame rate and supersample, which does change the
    pixels — those clips are cached separately from a full export's.
    """
    prof = profile or _full_profile(project)
    parts = [
        str(prof.width), str(prof.height), str(prof.fps),
        f"{entry.duration_seconds:.4f}",
        f"{prof.supersample:.2f}",
        unit.animation_preset.value,
        f"{unit.start_scale:.4f}{unit.end_scale:.4f}",
        f"{unit.start_x:.4f}{unit.start_y:.4f}{unit.end_x:.4f}{unit.end_y:.4f}",
        f"{unit.focus_x:.4f}{unit.focus_y:.4f}",
        unit.title, unit.subtitle,
        getattr(unit, "fact_note", ""),
        project.style.model_dump_json(),
        str(project.subtitles.burn_in),
        f"suppress_fade={suppress_fade_out}",
        project.export.intermediate_codec.value,
        str(image_path.stat().st_mtime_ns) if image_path and image_path.is_file() else "no-image",
        image_path.name if image_path else "none",
    ]
    if project.subtitles.burn_in:
        parts.extend(f"{c.start_seconds:.3f}-{c.end_seconds:.3f}:{c.text}" for c in cues)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def resolve_image(project: Project, unit: Scene | Section, paths: ProjectPaths) -> Path:
    """Find the source image for a section, or fail with a specific message.

    Intro and outro fall back to a scene image when they have none of their own.
    They are framing, not content: blocking an entire render because the intro
    lacks a dedicated picture would be a poor trade, and a content package
    rarely ships separate intro art.
    """
    filename = unit.image_file

    if not filename and isinstance(unit, Section):
        scenes_with_images = [s for s in project.active_scenes if s.image_file]
        if scenes_with_images:
            # The intro opens on the first image; the outro closes on the last.
            fallback = (
                scenes_with_images[0] if unit is project.intro else scenes_with_images[-1]
            )
            filename = fallback.image_file

    if not filename:
        label = _label(project, unit)
        raise ValidationError(
            ErrorCode.MISSING_IMAGE,
            f"{label} has no image.",
            suggestion=(
                "Upload an image and map it to this scene, or disable the scene so it "
                "is skipped."
            ),
        )

    path = safe_join(paths.images, filename)
    if not path.is_file():
        raise ValidationError(
            ErrorCode.MISSING_IMAGE,
            f"The image '{filename}' is missing from this project.",
            details=str(path),
            suggestion="Re-upload the image, or pick a different one for this scene.",
        )
    return path


def _label(project: Project, unit: Scene | Section) -> str:
    if unit is project.intro:
        return "The intro"
    if unit is project.outro:
        return "The outro"
    if isinstance(unit, Scene):
        return f"Scene {unit.order + 1}" + (f" ({unit.title})" if unit.title else "")
    return "A section"


async def render_scene_clip(
    project: Project,
    unit: Scene | Section,
    entry: TimelineEntry,
    paths: ProjectPaths,
    *,
    cues: list[Cue] | None = None,
    previous_preset: AnimationPreset | None = None,
    index: int = 0,
    settings: Settings | None = None,
    runner: FFmpegRunner | None = None,
    cancel_event: object | None = None,
    on_progress: object | None = None,
    suppress_fade_out: bool = False,
    profile: RenderProfile | None = None,
) -> SceneClip:
    """Render (or reuse) one scene's clip.

    ``suppress_fade_out`` is set for the final section: its fade to black is
    applied during assembly instead, so it lands at the true end of the video
    rather than before the closing hold.

    ``profile`` carries the render geometry (resolution, frame rate, supersample)
    and defaults to the project's own full-quality settings. Preview renders pass
    a lighter profile whose clips are cached in their own namespace, so a quick
    preview never evicts the clips a full export built.
    """
    active = settings or get_settings()
    ffmpeg_runner = runner or FFmpegRunner(active)
    ffmpeg = active.require_tool("ffmpeg")
    cue_list = cues or []
    prof = profile or _full_profile(project)

    source_image = resolve_image(project, unit, paths)
    key = cache_key(
        project, unit, entry, cue_list,
        image_path=source_image, suppress_fade_out=suppress_fade_out, profile=prof,
    )
    spec = intermediate_spec(project.export.intermediate_codec)
    # Preview clips live in their own subdirectory so the two caches never
    # collide and neither one's stale-clear can reach the other's files.
    clip_dir = paths.clips / prof.cache_slug if prof.cache_slug else paths.clips
    clip_dir.mkdir(parents=True, exist_ok=True)
    target = clip_dir / f"{entry.unit_id}-{key}{spec.suffix}"

    if target.is_file():
        logger.debug("reusing cached clip for %s", entry.unit_id)
        return SceneClip(
            path=target, unit_id=entry.unit_id,
            duration_seconds=entry.duration_seconds, reused=True, cache_key=key,
        )

    # Clear this unit's superseded clips (within this profile only) so the cache
    # does not grow forever. glob is non-recursive, so the full cache's sweep
    # never descends into the preview subdirectory, nor vice versa.
    for stale in clip_dir.glob(f"{entry.unit_id}-*"):
        stale.unlink(missing_ok=True)

    width, height, fps = prof.width, prof.height, prof.fps
    duration = entry.duration_seconds
    frames = max(1, int(round(duration * fps)))

    normalized = normalize_image(
        source_image,
        paths.normalized / f"{source_image.stem}-{width}x{height}.png",
        width=width, height=height, focus_x=unit.focus_x, focus_y=unit.focus_y,
    )

    motion = resolve_motion(unit, project_id=project.project_id, index=index,
                            previous=previous_preset)
    zoompan = build_zoompan_filter(
        motion, frames=frames, output_width=width, output_height=height,
        fps=fps, supersample=prof.supersample,
    )

    inputs: list[str] = ["-loop", "1", "-t", f"{duration:.4f}", "-i", str(normalized)]
    steps: list[str] = [f"[0:v]{zoompan},format=rgba[base]"]
    current = "base"
    input_index = 1

    def add_overlay_input(path: Path) -> int:
        nonlocal input_index
        inputs.extend(["-loop", "1", "-t", f"{duration:.4f}", "-i", str(path)])
        index_used = input_index
        input_index += 1
        return index_used

    # --- readability scrim ------------------------------------------------
    scrim = render_scrim(
        frame_width=width, frame_height=height,
        opacity=project.style.overlay_opacity, output_dir=paths.cards,
    )
    if scrim is not None and _has_any_text(unit, project):
        idx = add_overlay_input(scrim)
        steps.append(f"[{idx}:v]format=rgba[scrim]")
        steps.append(f"[{current}][scrim]overlay=0:0[scrimmed]")
        current = "scrimmed"

    # --- dark overlay for intro/outro ------------------------------------
    if isinstance(unit, Section) and unit.dark_overlay_opacity > 0:
        steps.append(
            f"[{current}]colorchannelmixer="
            f"rr={1 - unit.dark_overlay_opacity:.3f}:"
            f"gg={1 - unit.dark_overlay_opacity:.3f}:"
            f"bb={1 - unit.dark_overlay_opacity:.3f}[darkened]"
        )
        current = "darkened"

    # --- title and caption ------------------------------------------------
    title_card = render_card(
        unit.title, project.style.title,
        frame_width=width, frame_height=height,
        position=project.style.text_position, margin=project.style.text_safe_margin,
        output_dir=paths.cards,
    )
    caption_card = render_card(
        unit.subtitle, project.style.subtitle,
        frame_width=width, frame_height=height,
        position=project.style.text_position, margin=project.style.text_safe_margin,
        output_dir=paths.cards,
    )

    # Burned-in subtitles own the bottom band, so titles are lifted clear of it.
    # Without this the title and the caption render on top of each other.
    burn_offset = (
        -int(height * SUBTITLE_BAND_RATIO)
        if (project.subtitles.burn_in and cue_list and _is_bottom(project.style.text_position))
        else 0
    )

    if title_card is not None:
        current = _chain_card(
            steps, inputs, add_overlay_input, current, title_card,
            label="title",
            start=unit.title_timing.start_seconds,
            end=min(unit.title_timing.end_seconds, duration),
            style=project.style.title,
            y_offset=burn_offset,
        )
    if caption_card is not None:
        # The caption sits above the title when both share a bottom position.
        offset = burn_offset + (
            -(title_card.height - CAPTION_GAP) if title_card is not None else 0
        )
        current = _chain_card(
            steps, inputs, add_overlay_input, current, caption_card,
            label="caption",
            start=unit.subtitle_timing.start_seconds,
            end=min(unit.subtitle_timing.end_seconds, duration),
            style=project.style.subtitle,
            y_offset=offset,
        )

    # --- watermark ---------------------------------------------------------
    if project.style.watermark_text:
        watermark_style = project.style.caption.model_copy(
            update={"size": 24, "box": False, "shadow": True}
        )
        watermark = render_card(
            project.style.watermark_text, watermark_style,
            frame_width=width, frame_height=height,
            position=TextPosition.TOP_RIGHT, margin=project.style.text_safe_margin,
            output_dir=paths.cards,
        )
        if watermark is not None:
            idx = add_overlay_input(watermark.path)
            steps.append(
                f"[{idx}:v]format=rgba,colorchannelmixer=aa={project.style.watermark_opacity:.3f}[wm]"
            )
            steps.append(f"[{current}][wm]overlay={watermark.x}:{watermark.y}[withwm]")
            current = "withwm"

    # --- burned-in subtitles ----------------------------------------------
    if project.subtitles.burn_in and cue_list:
        current = await _apply_subtitles(
            project, entry, cue_list, paths, steps, inputs, add_overlay_input, current,
            width=width, height=height, duration=duration, fps=fps,
            runner=ffmpeg_runner, ffmpeg=ffmpeg, cancel_event=cancel_event,
        )

    # --- fades to and from black ------------------------------------------
    if isinstance(unit, Section):
        if unit.fade_from_black_seconds > 0:
            steps.append(f"[{current}]fade=t=in:st=0:d={unit.fade_from_black_seconds:.3f}[fadein]")
            current = "fadein"
        # The final section's fade-out is applied during assembly instead, after
        # the closing hold. Fading here would black the picture out before the
        # tail silence, leaving seconds of dead black at the end.
        if unit.fade_to_black_seconds > 0 and not suppress_fade_out:
            start = max(0.0, duration - unit.fade_to_black_seconds)
            steps.append(
                f"[{current}]fade=t=out:st={start:.3f}:d={unit.fade_to_black_seconds:.3f}[fadeout]"
            )
            current = "fadeout"

    steps.append(f"[{current}]format=yuv420p[v]")

    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        *progress_args(),
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", "[v]",
        *base_output_args(fps=fps),
        "-t", f"{duration:.4f}",
        *spec.args,
        "-an",
        str(target),
    ]

    log: list[str] = []
    await ffmpeg_runner.run(
        args,
        stage=f"scene-clip:{entry.unit_id}",
        expected_duration=duration,
        on_progress=on_progress,  # type: ignore[arg-type]
        log_sink=log.append,
        cancel_event=cancel_event,  # type: ignore[arg-type]
    )

    logger.info("rendered clip for %s (%.2fs, %d overlays)", entry.unit_id, duration, input_index - 1)
    return SceneClip(
        path=target, unit_id=entry.unit_id, duration_seconds=duration,
        reused=False, cache_key=key, log=log,
    )


def _has_any_text(unit: Scene | Section, project: Project) -> bool:
    return bool(unit.title or unit.subtitle or project.style.watermark_text)


def _is_bottom(position: TextPosition) -> bool:
    return position.value.startswith("bottom")


def _chain_card(
    steps: list[str],
    inputs: list[str],
    add_input,  # noqa: ANN001
    current: str,
    card: TextCard,
    *,
    label: str,
    start: float,
    end: float,
    style,  # noqa: ANN001
    y_offset: int,
) -> str:
    """Add one fading text overlay to the filter chain."""
    if end <= start:
        return current

    idx = add_input(card.path)
    fade_in = min(style.fade_in_seconds, max(0.01, (end - start) / 2))
    fade_out = min(style.fade_out_seconds, max(0.01, (end - start) / 2))
    fade_out_start = max(start, end - fade_out)

    # The overlay input is a looped still, so its own timeline starts at 0.
    steps.append(
        f"[{idx}:v]format=rgba,"
        f"fade=t=in:st={start:.3f}:d={fade_in:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}:alpha=1[{label}]"
    )
    steps.append(
        f"[{current}][{label}]overlay={card.x}:{card.y + y_offset}:"
        f"enable='between(t,{start:.3f},{end:.3f})'[with{label}]"
    )
    return f"with{label}"


async def _apply_subtitles(
    project: Project,
    entry: TimelineEntry,
    cues: list[Cue],
    paths: ProjectPaths,
    steps: list[str],
    inputs: list[str],
    add_input,  # noqa: ANN001
    current: str,
    *,
    width: int,
    height: int,
    duration: float,
    fps: int,
    runner: FFmpegRunner,
    ffmpeg: str,
    cancel_event: object | None,
) -> str:
    """Burn this scene's cues in, chaining or pre-compositing as needed."""
    style = project.style.subtitles
    position = TextPosition.BOTTOM_CENTER

    if len(cues) <= MAX_INLINE_SUBTITLE_OVERLAYS:
        for number, cue in enumerate(cues):
            card = render_card(
                cue.text, style,
                frame_width=width, frame_height=height,
                position=position, margin=project.style.text_safe_margin,
                output_dir=paths.cards,
            )
            if card is None:
                continue
            # Cue times are absolute; the clip's own timeline starts at zero.
            start = max(0.0, cue.start_seconds - entry.start_seconds)
            end = min(duration, cue.end_seconds - entry.start_seconds)
            current = _chain_card(
                steps, inputs, add_input, current, card,
                label=f"cue{number}", start=start, end=end, style=style, y_offset=0,
            )
        return current

    # Too many cues to chain inline: pre-composite them into one overlay clip.
    overlay = await _precompose_subtitle_track(
        project, entry, cues, paths,
        width=width, height=height, duration=duration, fps=fps,
        runner=runner, ffmpeg=ffmpeg, cancel_event=cancel_event,
    )
    idx = add_input(overlay)
    steps.append(f"[{idx}:v]format=rgba[subs]")
    steps.append(f"[{current}][subs]overlay=0:0[withsubs]")
    return "withsubs"


async def _precompose_subtitle_track(
    project: Project,
    entry: TimelineEntry,
    cues: list[Cue],
    paths: ProjectPaths,
    *,
    width: int,
    height: int,
    duration: float,
    fps: int,
    runner: FFmpegRunner,
    ffmpeg: str,
    cancel_event: object | None,
) -> Path:
    """Render every cue of a dense scene into one transparent overlay clip.

    Keeps the scene's own filtergraph to a single overlay however many cues it
    has, at the cost of one extra pass.
    """
    digest = hashlib.sha256(
        "".join(f"{c.start_seconds:.3f}{c.end_seconds:.3f}{c.text}" for c in cues).encode()
    ).hexdigest()[:16]
    target = paths.subtitle_assets / f"{entry.unit_id}-subs-{digest}.mov"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)

    style = project.style.subtitles
    inputs: list[str] = [
        "-f", "lavfi", "-t", f"{duration:.4f}",
        "-i", f"color=c=black@0.0:s={width}x{height}:r={fps},format=rgba",
    ]
    steps: list[str] = []
    current = "0:v"

    for number, cue in enumerate(cues):
        card = render_card(
            cue.text, style,
            frame_width=width, frame_height=height,
            position=TextPosition.BOTTOM_CENTER, margin=project.style.text_safe_margin,
            output_dir=paths.cards,
        )
        if card is None:
            continue
        start = max(0.0, cue.start_seconds - entry.start_seconds)
        end = min(duration, cue.end_seconds - entry.start_seconds)
        if end <= start:
            continue

        inputs.extend(["-loop", "1", "-t", f"{duration:.4f}", "-i", str(card.path)])
        source = f"{number + 1}:v"
        fade = min(0.25, (end - start) / 3)
        steps.append(
            f"[{source}]format=rgba,"
            f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(start, end - fade):.3f}:d={fade:.3f}:alpha=1[c{number}]"
        )
        steps.append(
            f"[{current}][c{number}]overlay={card.x}:{card.y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[s{number}]"
        )
        current = f"s{number}"

    steps.append(f"[{current}]format=rgba[out]")
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        *progress_args(),
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", "[out]",
        *base_output_args(fps=fps),
        "-t", f"{duration:.4f}",
        # QT RLE keeps the alpha channel, which H.264 cannot carry.
        "-c:v", "qtrle",
        str(target),
    ]
    await runner.run(
        args, stage=f"subtitle-track:{entry.unit_id}", cancel_event=cancel_event  # type: ignore[arg-type]
    )
    logger.info("pre-composited %d subtitle cues for %s", len(cues), entry.unit_id)
    return target
