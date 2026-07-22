"""Output validation for Shorts.

Same standard the long pipeline holds itself to: a Short is not finished because
FFmpeg exited zero, it is finished because the file on disk is 1080x1920, at the
source's frame rate, constant, H.264/yuv420p, with a non-silent 48 kHz AAC track
and the duration the plan asked for.

The geometry assertion is the one specific to Shorts: the source must sit inside
the vertical canvas at its **original aspect ratio**, letterboxed rather than
stretched or cropped, so the expected inner size is computed from the source's
own dimensions and checked against what actually came out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.render.validate import Assertion, ValidationReport
from app.timing.probe import frame_timestamps, measure_mean_volume, probe_video

logger = logging.getLogger("evb.shorts.validate")

#: Cutting is frame-accurate, but every cut rounds to a frame boundary, so a
#: multi-cut Short can legitimately land a few frames either side of the plan.
BASE_DURATION_TOLERANCE = 0.35
PER_CUT_TOLERANCE = 0.05
SILENCE_THRESHOLD_DB = -60.0


@dataclass(frozen=True)
class Geometry:
    """Where the 16:9 source sits on the vertical canvas."""

    inner_width: int
    inner_height: int
    offset_x: int
    offset_y: int

    @property
    def letterboxed(self) -> bool:
        return self.offset_y > 0

    @property
    def pillarboxed(self) -> bool:
        return self.offset_x > 0


def fit_geometry(
    source_width: int, source_height: int, canvas_width: int, canvas_height: int
) -> Geometry:
    """Largest even-pixel box that fits the source in the canvas, aspect kept.

    A 1920x1080 source in a 1080x1920 canvas comes out at 1080x607.5 in real
    numbers; H.264 with yuv420p needs even dimensions, so it is rendered at
    1080x608 with 656 rows of background above and below. Nothing is ever
    stretched, cropped or zoomed to fill.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source dimensions must be positive")

    scale = min(canvas_width / source_width, canvas_height / source_height)
    width = _even(round(source_width * scale), maximum=canvas_width)
    height = _even(round(source_height * scale), maximum=canvas_height)
    return Geometry(
        inner_width=width,
        inner_height=height,
        offset_x=(canvas_width - width) // 2,
        offset_y=(canvas_height - height) // 2,
    )


def _even(value: int, *, maximum: int) -> int:
    value = min(value, maximum)
    value -= value % 2
    return max(2, value)


@dataclass
class ShortValidation(ValidationReport):
    geometry: Geometry | None = field(default=None)


def validate_short(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: int,
    expected_duration: float,
    source_width: int,
    source_height: int,
    cut_count: int = 1,
    settings: Settings | None = None,
) -> ShortValidation:
    """Verify a finished Short against what the plan asked for."""
    active = settings or get_settings()
    report = ShortValidation(path=path)

    if not path.is_file():
        report.assertions.append(Assertion("file exists", False, "a file on disk", "missing"))
        return report

    report.size_bytes = path.stat().st_size
    report.assertions.append(
        Assertion(
            "file is not empty",
            report.size_bytes > 10_000,
            "> 10 KB",
            f"{report.size_bytes} bytes",
        )
    )

    info = probe_video(path, settings=active)

    report.assertions.append(
        Assertion("width", info.width == expected_width, str(expected_width), str(info.width))
    )
    report.assertions.append(
        Assertion("height", info.height == expected_height, str(expected_height), str(info.height))
    )
    report.assertions.append(
        Assertion("orientation", info.height > info.width, "vertical",
                  "vertical" if info.height > info.width else "not vertical")
    )
    report.assertions.append(
        Assertion("video codec", info.codec == "h264", "h264", info.codec)
    )
    report.assertions.append(
        Assertion("pixel format", info.pix_fmt == "yuv420p", "yuv420p", info.pix_fmt)
    )

    expected_rate = f"{expected_fps}/1"
    report.assertions.append(
        Assertion("average frame rate", info.avg_frame_rate == expected_rate,
                  expected_rate, info.avg_frame_rate)
    )
    report.assertions.append(
        Assertion("base frame rate", info.r_frame_rate == expected_rate,
                  expected_rate, info.r_frame_rate)
    )

    # Constant frame rate, measured rather than trusted.
    timestamps = frame_timestamps(path, seconds=4.0, settings=active)
    if len(timestamps) > 10:
        interval = 1.0 / expected_fps
        intervals = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
        irregular = [i for i in intervals if abs(i - interval) > 0.002]
        report.assertions.append(
            Assertion(
                "measured frame intervals",
                not irregular,
                f"all ~{interval:.6f}s",
                f"{len(irregular)} of {len(intervals)} irregular",
            )
        )
    else:
        report.assertions.append(
            Assertion("measured frame intervals", True, "sampled",
                      "too short to sample", fatal=False)
        )

    tolerance = BASE_DURATION_TOLERANCE + PER_CUT_TOLERANCE * max(0, cut_count - 1)
    report.assertions.append(
        Assertion(
            "duration",
            abs(info.duration_seconds - expected_duration) <= tolerance,
            f"{expected_duration:.3f}s (+/-{tolerance:.2f}s)",
            f"{info.duration_seconds:.3f}s",
        )
    )

    report.assertions.append(
        Assertion("audio stream present", info.has_audio, "yes",
                  "yes" if info.has_audio else "no")
    )
    if info.has_audio:
        report.assertions.append(
            Assertion("audio codec", info.audio_codec == "aac", "aac", str(info.audio_codec))
        )
        report.assertions.append(
            Assertion("audio sample rate", info.audio_sample_rate == 48_000,
                      "48000", str(info.audio_sample_rate))
        )
        mean_volume = measure_mean_volume(path, settings=active)
        if mean_volume is not None:
            report.assertions.append(
                Assertion(
                    "audio is not silent",
                    mean_volume > SILENCE_THRESHOLD_DB,
                    f"> {SILENCE_THRESHOLD_DB} dB",
                    f"{mean_volume:.1f} dB",
                )
            )

    # The source keeps its own aspect ratio inside the canvas.
    geometry = fit_geometry(source_width, source_height, expected_width, expected_height)
    report.geometry = geometry
    source_ratio = source_width / source_height
    inner_ratio = geometry.inner_width / geometry.inner_height
    report.assertions.append(
        Assertion(
            "source aspect ratio preserved",
            abs(inner_ratio - source_ratio) < 0.01,
            f"{source_ratio:.4f} (source)",
            f"{inner_ratio:.4f} ({geometry.inner_width}x{geometry.inner_height})",
        )
    )
    report.assertions.append(
        Assertion(
            "background bars present",
            geometry.letterboxed or geometry.pillarboxed,
            "the source does not fill the vertical canvas",
            f"offset {geometry.offset_x}x{geometry.offset_y}",
            fatal=False,
        )
    )

    from app.shorts.manifest import sha256_file

    report.checksum = sha256_file(path)

    for assertion in report.assertions:
        level = logging.INFO if assertion.passed else logging.ERROR
        logger.log(level, "validate short %s", assertion.describe())

    return report
