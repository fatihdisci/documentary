#!/usr/bin/env python
"""Benchmark intermediate codecs on real pan-and-zoom content.

The numbers in ``app/render/codecs.py`` come from this script. Re-run it on a
new machine if you want to re-tune the default:

    backend/.venv/bin/python scripts/benchmark_codecs.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.models.enums import AnimationPreset, IntermediateCodec  # noqa: E402
from app.models.project import Scene  # noqa: E402
from app.render.codecs import INTERMEDIATE_SPECS  # noqa: E402
from app.render.kenburns import build_zoompan_filter, resolve_motion  # noqa: E402

SECONDS = 20
FPS = 60


def main() -> int:
    settings = get_settings()
    ffmpeg = settings.require_tool("ffmpeg")
    workdir = settings.temp_dir / "codec-benchmark"
    workdir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.factories import make_image_bytes

    source = workdir / "source.png"
    source.write_bytes(make_image_bytes(2560, 1440, seed=7))

    motion = resolve_motion(
        Scene(animation_preset=AnimationPreset.PAN_LEFT_TO_RIGHT), project_id="bench", index=0
    )
    zoompan = build_zoompan_filter(
        motion,
        frames=SECONDS * FPS,
        output_width=1920,
        output_height=1080,
        fps=FPS,
        supersample=3.0,
    )

    print(f"Encoding {SECONDS}s of 1080p{FPS} pan-and-zoom with each intermediate codec.\n")
    print(f"{'codec':<18}{'encode':>9}{'size':>10}{'GB / 7min video':>18}")
    print("-" * 55)

    results: list[tuple[str, float, float]] = []
    for codec, spec in INTERMEDIATE_SPECS.items():
        target = workdir / f"bench-{spec.name}{spec.suffix}"
        args = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-i", str(source),
            "-vf", zoompan,
            "-frames:v", str(SECONDS * FPS),
            "-r", str(FPS), "-fps_mode", "cfr",
            *spec.args,
            "-an",
            str(target),
        ]
        started = time.perf_counter()
        completed = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
        elapsed = time.perf_counter() - started

        if completed.returncode != 0:
            print(f"{spec.name:<18}{'FAILED':>9}   {completed.stderr.strip()[:60]}")
            continue

        size_mb = target.stat().st_size / 1_048_576
        per_minute = size_mb * (60 / SECONDS)
        # A 7-minute video holds every scene clip plus the final output.
        gb_for_seven = per_minute * 7 / 1024
        print(f"{spec.name:<18}{elapsed:>8.1f}s{size_mb:>9.1f}M{gb_for_seven:>17.2f}")
        results.append((spec.name, elapsed, per_minute))
        target.unlink(missing_ok=True)

    if results:
        print("\nMB per minute of 1080p60 (update codecs.py with these):")
        for name, _, per_minute in results:
            print(f"  {name:<18}{per_minute:>8.0f}")

    source.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
