"""Test helpers for building real assets — no mocks where a real file will do."""

from __future__ import annotations

import io
import json
import math
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def make_image_bytes(
    width: int = 1920,
    height: int = 1080,
    *,
    fmt: str = "PNG",
    label: str = "",
    seed: int = 0,
) -> bytes:
    """A real, decodable image with visible structure.

    Structure matters: a flat colour would let a broken pan/zoom or transition
    pass unnoticed in later milestones.
    """
    image = Image.new("RGB", (width, height), (18 + seed * 13 % 200, 40, 70))
    draw = ImageDraw.Draw(image)
    step = max(24, width // 24)
    for x in range(0, width, step):
        shade = 40 + (x // step * 17 + seed * 31) % 180
        draw.rectangle([x, 0, x + step // 2, height], fill=(shade, shade // 2, 200 - shade // 2))
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=(220, 220, 220), width=2)
    # Corner markers make cropping and panning errors obvious in a frame dump.
    marker = max(20, width // 30)
    for cx, cy, colour in (
        (0, 0, (255, 0, 0)),
        (width - marker, 0, (0, 255, 0)),
        (0, height - marker, (0, 0, 255)),
        (width - marker, height - marker, (255, 255, 0)),
    ):
        draw.rectangle([cx, cy, cx + marker, cy + marker], fill=colour)
    if label:
        draw.text((width // 2, height // 2), label, fill=(255, 255, 255))

    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def write_images(directory: Path, count: int = 10, *, prefix: str = "") -> list[Path]:
    """Write ``count`` numbered images, named the way a real package would be."""
    names = [
        "opening", "habitat", "anatomy", "diet", "arrival",
        "predators", "forest", "last-sighting", "bones", "conservation",
    ]
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index in range(count):
        label = names[index] if index < len(names) else f"scene-{index + 1}"
        name = f"{prefix}{index + 1:02d}-{label}.png"
        path = directory / name
        path.write_bytes(make_image_bytes(1920, 1080, label=label, seed=index))
        written.append(path)
    return written


def make_wav_bytes(seconds: float, *, freq: float = 220.0, sample_rate: int = 48_000) -> bytes:
    """A real WAV file of a known duration, so ffprobe has something true to measure."""
    frames = int(seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        samples = bytearray()
        for n in range(frames):
            # A gentle envelope avoids clicks at the boundaries.
            envelope = min(1.0, n / (sample_rate * 0.01), (frames - n) / (sample_rate * 0.01))
            value = int(12000 * envelope * math.sin(2 * math.pi * freq * n / sample_rate))
            samples += struct.pack("<h", value)
        handle.writeframes(bytes(samples))
    return buffer.getvalue()


def load_dodo_package() -> dict:
    return json.loads((FIXTURES / "dodo-content.json").read_text("utf-8"))
