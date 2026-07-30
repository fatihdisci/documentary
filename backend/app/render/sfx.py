"""Small deterministic UI sound effects for the branded openings.

The sounds are synthesized locally into cached 48 kHz PCM WAV files. This keeps
the house sound available offline, avoids licensing an asset pack, and makes a
render reproducible on every machine.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import wave
from pathlib import Path

from app.models.enums import IntroStyle
from app.models.project import LongIntro, ShortHook

SAMPLE_RATE = 48_000
SFX_RENDERER_VERSION = 1


def _add_click(samples: list[float], at: float, *, strength: float = 1.0) -> None:
    start = max(0, int(round(at * SAMPLE_RATE)))
    length = int(0.038 * SAMPLE_RATE)
    for offset in range(length):
        index = start + offset
        if index >= len(samples):
            break
        t = offset / SAMPLE_RATE
        envelope = math.exp(-t * 95.0)
        # A dry mechanical clack: two inharmonic partials and deterministic grit.
        grit = math.sin(offset * 12.9898) * math.sin(offset * 0.731)
        value = (
            0.18 * math.sin(2 * math.pi * 1_850 * t)
            + 0.13 * math.sin(2 * math.pi * 2_730 * t)
            + 0.08 * grit
        )
        samples[index] += value * envelope * strength


def _add_thump(samples: list[float], at: float, *, strength: float = 1.0) -> None:
    start = max(0, int(round(at * SAMPLE_RATE)))
    length = int(0.48 * SAMPLE_RATE)
    for offset in range(length):
        index = start + offset
        if index >= len(samples):
            break
        t = offset / SAMPLE_RATE
        low = math.sin(2 * math.pi * (72 - 24 * t) * t) * math.exp(-t * 9.0)
        body = math.sin(2 * math.pi * 118 * t) * math.exp(-t * 16.0)
        crack = math.sin(offset * 7.123) * math.exp(-t * 75.0)
        samples[index] += (0.44 * low + 0.18 * body + 0.11 * crack) * strength


def _add_whoosh(samples: list[float], start_seconds: float, end_seconds: float) -> None:
    start = max(0, int(round(start_seconds * SAMPLE_RATE)))
    end = min(len(samples), int(round(end_seconds * SAMPLE_RATE)))
    length = max(1, end - start)
    phase = 0.0
    for offset, index in enumerate(range(start, end)):
        progress = offset / length
        frequency = 210 + 720 * progress**2
        phase += 2 * math.pi * frequency / SAMPLE_RATE
        envelope = math.sin(math.pi * progress) * (0.25 + 0.75 * progress)
        airy = math.sin(offset * 5.731) * math.sin(offset * 0.117)
        samples[index] += (0.055 * math.sin(phase) + 0.035 * airy) * envelope


def _write_wav(target: Path, samples: list[float]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial.wav")
    with wave.open(str(partial), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for sample in samples:
            pcm = int(round(max(-0.92, min(0.92, sample)) * 32767))
            frames.extend(struct.pack("<hh", pcm, pcm))
        output.writeframes(frames)
    os.replace(partial, target)
    return target


def build_long_intro_sfx(intro: LongIntro, *, output_dir: Path) -> Path | None:
    """Typewriter clacks followed by the branded stamp impact."""
    if (
        not intro.enabled
        or intro.intro_style is not IntroStyle.TYPEWRITER_STAMP
        or intro.duration <= 0
    ):
        return None

    digest = hashlib.sha256(
        f"{SFX_RENDERER_VERSION}:long:{intro.model_dump_json()}".encode()
    ).hexdigest()[:20]
    target = output_dir / f"long-intro-sfx-{digest}.wav"
    if target.is_file():
        return target

    samples = [0.0] * max(1, int(round(intro.duration * SAMPLE_RATE)))
    title = intro.primary_title.strip()
    if title and intro.typewriter_duration > 0:
        steps = min(28, len(title))
        for index in range(steps):
            at = intro.typewriter_duration * index / max(1, steps)
            _add_click(samples, at, strength=0.72 + 0.16 * (index % 3 == 0))
    if intro.stamp_text.strip() and intro.stamp_at < intro.duration:
        _add_thump(samples, intro.stamp_at, strength=0.9)
    return _write_wav(target, samples)


def build_short_hook_sfx(
    hook: ShortHook,
    *,
    impact_at: float,
    total_duration_seconds: float,
    output_dir: Path,
) -> Path | None:
    """A short rise into the hook's visual impact beat."""
    if not hook.is_visible or total_duration_seconds <= 0:
        return None
    digest = hashlib.sha256(
        (
            f"{SFX_RENDERER_VERSION}:short:{hook.model_dump_json()}:"
            f"{impact_at:.4f}:{total_duration_seconds:.4f}"
        ).encode()
    ).hexdigest()[:20]
    target = output_dir / f"short-hook-sfx-{digest}.wav"
    if target.is_file():
        return target

    effect_duration = min(
        total_duration_seconds,
        max(hook.start_seconds + hook.duration_seconds, impact_at + 0.5),
    )
    samples = [0.0] * max(1, int(round(effect_duration * SAMPLE_RATE)))
    start = max(0.0, hook.start_seconds)
    impact = max(start, min(impact_at, total_duration_seconds))
    _add_click(samples, start, strength=0.35)
    if impact > start + 0.03:
        _add_whoosh(samples, start + 0.03, impact)
    _add_thump(samples, impact, strength=0.58)
    return _write_wav(target, samples)
