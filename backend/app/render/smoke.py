"""The render smoke test.

Renders a minimal but *complete* video — two scenes with Ken Burns motion, a
Pillow title overlay, a burned subtitle, one transition, narration and
background audio — at the project's real resolution and frame rate.

This exists to prove the whole approach works on this machine before the full
pipeline is built on top of it. It is deliberately self-contained: it does not
import the scene-clip or assembly modules, so it stays a genuinely independent
check rather than a test of the same code twice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.models.enums import AnimationPreset, TextPosition
from app.models.project import Scene, TextStyle
from app.render.ffmpeg import FFmpegRunner, base_output_args
from app.render.kenburns import build_zoompan_filter, resolve_motion
from app.render.text import render_card, render_scrim
from app.timing.probe import probe_video

logger = logging.getLogger("evb.smoke")


@dataclass
class SmokeResult:
    output: Path
    width: int
    height: int
    fps: float
    duration_seconds: float
    has_audio: bool
    log: list[str]


async def render_smoke_video(
    *,
    workdir: Path,
    images: list[Path],
    narration: list[Path],
    output: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
    scene_seconds: float = 3.0,
    transition_seconds: float = 0.6,
    supersample: float = 3.0,
    settings: Settings | None = None,
) -> SmokeResult:
    """Render the two-scene proof video. Returns its measured properties."""
    active = settings or get_settings()
    runner = FFmpegRunner(active)
    ffmpeg = active.require_tool("ffmpeg")
    workdir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    if len(images) < 2:
        raise ValueError("the smoke test needs two images")

    frames = int(round(scene_seconds * fps))
    titles = ["A Bird Without Fear", "The Ships Arrive"]
    captions = ["Mauritius, before 1598", "Dutch vessels made landfall in 1598"]

    scrim = render_scrim(
        frame_width=width, frame_height=height, opacity=0.55, output_dir=workdir
    )
    assert scrim is not None

    # --- Pass A: one clip per scene -------------------------------------
    clips: list[Path] = []
    for index, image in enumerate(images[:2]):
        motion = resolve_motion(
            Scene(
                animation_preset=(
                    AnimationPreset.SLOW_ZOOM_IN if index == 0 else AnimationPreset.PAN_LEFT_TO_RIGHT
                )
            ),
            project_id="smoke",
            index=index,
        )
        zoompan = build_zoompan_filter(
            motion,
            frames=frames,
            output_width=width,
            output_height=height,
            fps=fps,
            supersample=supersample,
        )

        title = render_card(
            titles[index],
            TextStyle(size=64, font_weight=700, box=False, shadow=True, shadow_blur=16),
            frame_width=width, frame_height=height,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=workdir,
        )
        caption = render_card(
            captions[index],
            TextStyle(size=34, font_weight=400, box=False, color="#C8D4E0"),
            frame_width=width, frame_height=height,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=workdir,
        )
        assert title is not None and caption is not None

        clip = workdir / f"smoke-scene-{index}.mp4"
        # Inputs: source image, scrim, title card, subtitle card.
        args = [
            ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-loop", "1", "-t", f"{scene_seconds}", "-i", str(image),
            "-loop", "1", "-t", f"{scene_seconds}", "-i", str(scrim),
            "-loop", "1", "-t", f"{scene_seconds}", "-i", str(title.path),
            "-loop", "1", "-t", f"{scene_seconds}", "-i", str(caption.path),
            "-filter_complex", ";".join(
                [
                    f"[0:v]{zoompan},format=rgba[bg]",
                    "[1:v]format=rgba[scrim]",
                    "[bg][scrim]overlay=0:0[scrimmed]",
                    # Alpha fades in and out, so text does not pop on or off.
                    f"[2:v]format=rgba,fade=t=in:st=0.4:d=0.5:alpha=1,"
                    f"fade=t=out:st={scene_seconds - 0.6:.3f}:d=0.5:alpha=1[title]",
                    f"[3:v]format=rgba,fade=t=in:st=0.8:d=0.5:alpha=1,"
                    f"fade=t=out:st={scene_seconds - 0.6:.3f}:d=0.5:alpha=1[caption]",
                    f"[scrimmed][title]overlay={title.x}:{title.y}:"
                    f"enable='between(t,0.4,{scene_seconds:.3f})'[withtitle]",
                    f"[withtitle][caption]overlay={caption.x}:{caption.y - title.height + 90}:"
                    f"enable='between(t,0.8,{scene_seconds:.3f})',format=yuv420p[v]",
                ]
            ),
            "-map", "[v]",
            *base_output_args(fps=fps),
            "-t", f"{scene_seconds}",
            "-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(clip),
        ]
        await runner.run(args, stage=f"smoke-scene-{index}", log_sink=log.append)
        clips.append(clip)

    # --- Pass B: transition, audio, final encode -------------------------
    # The timeline: scene 0 runs 0..scene_seconds, scene 1 starts one transition
    # early, and the total is shortened by exactly that overlap.
    offset = scene_seconds - transition_seconds
    total = scene_seconds * 2 - transition_seconds

    tracks = narration[:2]
    narration_inputs: list[str] = []
    for path in tracks:
        narration_inputs += ["-i", str(path)]

    # Input indices: 0 and 1 are the scene clips, then the narration tracks,
    # then the synthesized music bed last.
    music_index = 2 + len(tracks)

    audio_filters: list[str] = []
    if tracks:
        # Narration is placed at absolute times taken from the same timeline the
        # video uses, so audio and picture cannot drift apart.
        starts_ms = [300, int((offset + 0.3) * 1000)]
        for index in range(len(tracks)):
            audio_filters.append(
                f"[{2 + index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"adelay={starts_ms[index]}|{starts_ms[index]},"
                f"afade=t=in:st=0:d=0.02[n{index}]"
            )
        mix_inputs = "".join(f"[n{i}]" for i in range(len(tracks)))
        audio_filters.append(
            f"{mix_inputs}amix=inputs={len(tracks)}:normalize=0:dropout_transition=0[speech]"
        )
        # A quiet synthesized bed stands in for background music, so the mixing
        # path is genuinely exercised rather than skipped.
        audio_filters.append(f"[{music_index}:a]volume=-26dB[bed]")
        audio_filters.append("[speech][bed]amix=inputs=2:normalize=0:duration=first[aout]")

    filter_parts = [
        f"[0:v][1:v]xfade=transition=fade:duration={transition_seconds}:offset={offset:.3f},"
        f"format=yuv420p,fps={fps}[v]",
        *audio_filters,
    ]

    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(clips[0]),
        "-i", str(clips[1]),
        *narration_inputs,
        "-f", "lavfi", "-t", f"{total:.3f}", "-i", "sine=frequency=110:sample_rate=48000",
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]",
        *(["-map", "[aout]"] if tracks else []),
        *base_output_args(fps=fps),
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    await runner.run(args, stage="smoke-assemble", log_sink=log.append)

    info = probe_video(output, settings=active)
    logger.info(
        "smoke render complete: %dx%d @ %s, %.2fs, audio=%s",
        info.width, info.height, info.avg_frame_rate, info.duration_seconds, info.has_audio,
    )
    return SmokeResult(
        output=output,
        width=info.width,
        height=info.height,
        fps=info.avg_fps,
        duration_seconds=info.duration_seconds,
        has_audio=info.has_audio,
        log=log,
    )


def run_smoke_sync(**kwargs: object) -> SmokeResult:
    """Convenience wrapper for scripts and tests."""
    return asyncio.run(render_smoke_video(**kwargs))  # type: ignore[arg-type]
