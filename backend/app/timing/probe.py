"""Media probing with ffprobe.

Durations are always *measured*, never taken from a TTS provider's estimate or
inferred from text length. Every timing decision downstream depends on this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import ErrorCode, ValidationError
from app.render.ffmpeg import FFmpegRunner

logger = logging.getLogger("evb.probe")


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int
    codec: str
    bit_rate: int | None

    @property
    def is_silent_length(self) -> bool:
        return self.duration_seconds < 0.05


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    duration_seconds: float
    avg_frame_rate: str
    r_frame_rate: str
    codec: str
    pix_fmt: str
    nb_frames: int | None
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None

    @property
    def avg_fps(self) -> float:
        return _parse_rate(self.avg_frame_rate)

    @property
    def real_fps(self) -> float:
        return _parse_rate(self.r_frame_rate)


def _parse_rate(rate: str) -> float:
    """Parse ffprobe's ``num/den`` frame-rate notation."""
    if not rate or rate in {"0/0", "N/A"}:
        return 0.0
    if "/" in rate:
        numerator, _, denominator = rate.partition("/")
        try:
            den = float(denominator)
            return float(numerator) / den if den else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def probe_audio(path: Path, *, settings: Settings | None = None) -> AudioInfo:
    """Measure an audio file. Raises a clear error if it is unusable."""
    runner = FFmpegRunner(settings or get_settings())
    data = runner.probe_json(path)

    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ValidationError(
            ErrorCode.CORRUPT_AUDIO,
            f"'{path.name}' contains no audio stream.",
            details=f"streams found: {[s.get('codec_type') for s in data.get('streams', [])]}",
            suggestion="Check the file plays elsewhere, then re-export it as WAV or MP3.",
        )

    stream = streams[0]
    duration = _duration_from(data, stream)
    if duration <= 0:
        raise ValidationError(
            ErrorCode.CORRUPT_AUDIO,
            f"'{path.name}' reports a duration of zero.",
            details=f"format={data.get('format', {})}",
            suggestion="The file is likely truncated. Re-export it and try again.",
        )

    return AudioInfo(
        path=path,
        duration_seconds=duration,
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        codec=str(stream.get("codec_name") or "unknown"),
        bit_rate=int(stream["bit_rate"]) if stream.get("bit_rate", "").isdigit() else None,
    )


def probe_video(path: Path, *, settings: Settings | None = None) -> VideoInfo:
    """Measure a video file, including whether it carries usable audio."""
    runner = FFmpegRunner(settings or get_settings())
    data = runner.probe_json(path)

    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise ValidationError(
            ErrorCode.OUTPUT_VALIDATION_FAILED,
            f"'{path.name}' contains no video stream.",
            details=str(data.get("streams")),
        )
    video = video_streams[0]
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    audio = audio_streams[0] if audio_streams else None

    nb_frames_raw = video.get("nb_frames")
    nb_frames = int(nb_frames_raw) if str(nb_frames_raw).isdigit() else None

    return VideoInfo(
        path=path,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_seconds=_duration_from(data, video),
        avg_frame_rate=str(video.get("avg_frame_rate") or ""),
        r_frame_rate=str(video.get("r_frame_rate") or ""),
        codec=str(video.get("codec_name") or "unknown"),
        pix_fmt=str(video.get("pix_fmt") or "unknown"),
        nb_frames=nb_frames,
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
    )


def _duration_from(data: dict, stream: dict) -> float:
    """Prefer the stream duration, fall back to the container's."""
    for source in (stream.get("duration"), data.get("format", {}).get("duration")):
        try:
            value = float(source)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def frame_timestamps(
    path: Path, *, count: int = 120, settings: Settings | None = None
) -> list[float]:
    """Read the first ``count`` frame presentation timestamps.

    Used to corroborate constant frame rate by measuring real intervals, rather
    than trusting the container's advertised rate alone.
    """
    runner = FFmpegRunner(settings or get_settings())
    ffprobe = (settings or get_settings()).require_tool("ffprobe")
    result = runner._run_sync(  # noqa: SLF001 - internal helper, same module family
        [
            ffprobe, "-hide_banner", "-loglevel", "error",
            "-select_streams", "v:0",
            "-show_entries", "frame=pts_time",
            "-read_intervals", f"%+#{count}",
            "-print_format", "csv=p=0",
            str(path),
        ],
        timeout=120.0,
    )
    if not result.ok:
        logger.warning("could not read frame timestamps from %s: %s", path.name, result.stderr[:200])
        return []

    timestamps: list[float] = []
    for line in result.stdout.splitlines():
        value = line.strip().rstrip(",")
        if not value:
            continue
        try:
            timestamps.append(float(value))
        except ValueError:
            continue
    return timestamps


def measure_mean_volume(path: Path, *, settings: Settings | None = None) -> float | None:
    """Return the mean volume in dBFS, or None if it could not be measured.

    Used to assert a rendered file's audio is not digital silence.
    """
    active = settings or get_settings()
    runner = FFmpegRunner(active)
    ffmpeg = active.require_tool("ffmpeg")
    result = runner._run_sync(  # noqa: SLF001
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        timeout=180.0,
    )
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None
