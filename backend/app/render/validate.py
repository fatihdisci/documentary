"""Output validation.

A render is not successful because FFmpeg exited zero. It is successful because
the file on disk has the resolution, frame rate, codecs, duration and audio it
was supposed to have. Every assertion records the value actually found, so a
failure report is immediately actionable.

Two deliberate leniencies, per the project brief:

* ``nb_frames`` is checked **only when the container reports it**, with a small
  tolerance. It is optional metadata and a missing value never fails a render.
* Constant frame rate is corroborated by **measuring real frame timestamps**,
  not by trusting the advertised rate alone.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.models.project import Project
from app.render.codecs import RenderProfile
from app.timing.probe import frame_timestamps, measure_mean_volume, probe_video
from app.timing.schedule import Timeline

logger = logging.getLogger("evb.validate")

#: How far the file's duration may differ from the computed timeline.
DURATION_TOLERANCE_SECONDS = 0.30
#: nb_frames tolerance, when the field is present at all.
FRAME_COUNT_TOLERANCE = 3
#: Below this mean volume the audio track is effectively silence.
SILENCE_THRESHOLD_DB = -60.0


@dataclass
class Assertion:
    name: str
    passed: bool
    expected: str
    actual: str
    #: Warnings are reported but do not fail the render.
    fatal: bool = True

    def describe(self) -> str:
        status = "ok" if self.passed else ("FAILED" if self.fatal else "warning")
        return f"[{status}] {self.name}: expected {self.expected}, found {self.actual}"


@dataclass
class ValidationReport:
    path: Path
    assertions: list[Assertion] = field(default_factory=list)
    checksum: str = ""
    size_bytes: int = 0

    @property
    def failures(self) -> list[Assertion]:
        return [a for a in self.assertions if not a.passed and a.fatal]

    @property
    def warnings(self) -> list[str]:
        return [a.describe() for a in self.assertions if not a.passed and not a.fatal]

    @property
    def passed(self) -> bool:
        return not self.failures

    def format_failures(self) -> str:
        lines = [a.describe() for a in self.assertions]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "sizeBytes": self.size_bytes,
            "checksum": self.checksum,
            "assertions": [
                {
                    "name": a.name,
                    "passed": a.passed,
                    "expected": a.expected,
                    "actual": a.actual,
                    "fatal": a.fatal,
                }
                for a in self.assertions
            ],
        }


def validate_output(
    path: Path,
    *,
    project: Project,
    timeline: Timeline,
    settings: Settings | None = None,
    check_audio: bool = True,
    profile: RenderProfile | None = None,
) -> ValidationReport:
    """Verify a rendered file against what the project asked for.

    ``profile`` is the geometry this render actually targeted; it defaults to the
    project's own video settings. A preview renders at a lower frame rate, so the
    file is checked against the *preview's* rate, not the project's 60 fps.
    """
    active = settings or get_settings()
    exp_width = profile.width if profile else project.video.width
    exp_height = profile.height if profile else project.video.height
    exp_fps = profile.fps if profile else project.video.fps
    report = ValidationReport(path=path)

    if not path.is_file():
        report.assertions.append(
            Assertion("file exists", False, "a file on disk", "missing")
        )
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
        Assertion("width", info.width == exp_width,
                  str(exp_width), str(info.width))
    )
    report.assertions.append(
        Assertion("height", info.height == exp_height,
                  str(exp_height), str(info.height))
    )
    report.assertions.append(
        Assertion("video codec", info.codec == "h264", "h264", info.codec)
    )
    report.assertions.append(
        Assertion("pixel format", info.pix_fmt == "yuv420p", "yuv420p", info.pix_fmt)
    )

    expected_rate = f"{exp_fps}/1"
    report.assertions.append(
        Assertion("average frame rate", info.avg_frame_rate == expected_rate,
                  expected_rate, info.avg_frame_rate)
    )
    report.assertions.append(
        Assertion("base frame rate", info.r_frame_rate == expected_rate,
                  expected_rate, info.r_frame_rate)
    )

    # Corroborate CFR by measurement rather than trusting the header.
    timestamps = frame_timestamps(path, seconds=4.0, settings=active)
    if len(timestamps) > 10:
        expected_interval = 1.0 / exp_fps
        intervals = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
        irregular = [i for i in intervals if abs(i - expected_interval) > 0.002]
        report.assertions.append(
            Assertion(
                "measured frame intervals",
                not irregular,
                f"all ~{expected_interval:.6f}s",
                f"{len(irregular)} of {len(intervals)} irregular",
            )
        )
    else:
        report.assertions.append(
            Assertion(
                "measured frame intervals", True, "sampled", "too short to sample", fatal=False
            )
        )

    # nb_frames is optional metadata: only checked when present.
    if info.nb_frames is not None:
        expected_frames = round(info.duration_seconds * exp_fps)
        difference = abs(info.nb_frames - expected_frames)
        report.assertions.append(
            Assertion(
                "frame count",
                difference <= FRAME_COUNT_TOLERANCE,
                f"{expected_frames} (+/-{FRAME_COUNT_TOLERANCE})",
                str(info.nb_frames),
            )
        )
    else:
        report.assertions.append(
            Assertion(
                "frame count",
                True,
                "checked only when reported",
                "not reported by this container",
                fatal=False,
            )
        )

    expected_duration = timeline.total_duration_seconds
    report.assertions.append(
        Assertion(
            "duration",
            abs(info.duration_seconds - expected_duration) <= DURATION_TOLERANCE_SECONDS,
            f"{expected_duration:.3f}s (+/-{DURATION_TOLERANCE_SECONDS}s)",
            f"{info.duration_seconds:.3f}s",
        )
    )

    # Narration must not have been cut off by the end of the file.
    last_narration = timeline.last_narration_end
    if last_narration > 0:
        report.assertions.append(
            Assertion(
                "narration is not truncated",
                info.duration_seconds >= last_narration - 0.05,
                f"at least {last_narration:.3f}s",
                f"{info.duration_seconds:.3f}s",
            )
        )

    if check_audio and timeline.narration_duration_seconds > 0:
        report.assertions.append(
            Assertion("audio stream present", info.has_audio, "yes", "yes" if info.has_audio else "no")
        )
        if info.has_audio:
            report.assertions.append(
                Assertion("audio codec", info.audio_codec == "aac", "aac", str(info.audio_codec))
            )
            report.assertions.append(
                Assertion(
                    "audio sample rate",
                    info.audio_sample_rate == 48_000,
                    "48000",
                    str(info.audio_sample_rate),
                )
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

    report.checksum = _sha256(path)

    for assertion in report.assertions:
        level = logging.INFO if assertion.passed else logging.ERROR
        logger.log(level, "validate %s", assertion.describe())

    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
