"""A synthesized ambient bed.

Deliberately basic: a slow, low drone with a gentle movement on top. It exists
so the app works with zero external assets — for tests, for a first render, and
for demonstrating the mixing path — not as a substitute for real music.

The UI labels it "basic generated ambient bed" and it is never selected
automatically; the user picks it explicitly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings, get_settings
from app.render.ffmpeg import FFmpegRunner

logger = logging.getLogger("evb.music")

SAMPLE_RATE = 48_000


def ambient_lavfi(duration_seconds: float) -> str:
    """A lavfi graph producing a calm drone of the requested length.

    Three detuned sines an octave apart, slowly tremolo'd and low-passed so the
    result sits under narration without drawing attention.
    """
    duration = max(1.0, duration_seconds)
    return (
        f"sine=frequency=55:sample_rate={SAMPLE_RATE}:duration={duration:.3f}[a];"
        f"sine=frequency=82.5:sample_rate={SAMPLE_RATE}:duration={duration:.3f}[b];"
        f"sine=frequency=110.3:sample_rate={SAMPLE_RATE}:duration={duration:.3f}[c];"
        f"[a]volume=0.5[a1];"
        f"[b]volume=0.28[b1];"
        f"[c]volume=0.16[c1];"
        f"[a1][b1][c1]amix=inputs=3:normalize=0[mixed];"
        # Slow tremolo gives it some life; the low-pass removes the buzz.
        # 0.1 Hz is the filter's minimum, and slow enough to read as a swell.
        f"[mixed]tremolo=f=0.1:d=0.25,lowpass=f=520,"
        f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo[out]"
    )


async def render_ambient_bed(
    duration_seconds: float,
    target: Path,
    *,
    settings: Settings | None = None,
    runner: FFmpegRunner | None = None,
) -> Path:
    """Render the ambient bed to a WAV file of exactly ``duration_seconds``."""
    active = settings or get_settings()
    ffmpeg_runner = runner or FFmpegRunner(active)
    ffmpeg = active.require_tool("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)

    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-filter_complex", ambient_lavfi(duration_seconds),
        "-map", "[out]",
        "-t", f"{duration_seconds:.3f}",
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
        str(target),
    ]
    await ffmpeg_runner.run(args, stage="ambient-bed")
    logger.info("rendered %.1fs ambient bed -> %s", duration_seconds, target.name)
    return target
