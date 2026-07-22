"""Media probing with ffprobe.

Durations are always *measured*, never taken from a TTS provider's estimate or
inferred from text length. Every timing decision downstream depends on this.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, ValidationError
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
            f"'{path.name}' içinde ses yok.",
            details=f"bulunan kanallar: {[s.get('codec_type') for s in data.get('streams', [])]}",
            suggestion="Dosyanın başka bir oynatıcıda çaldığını doğrulayın, sonra WAV ya da MP3 olarak yeniden kaydedin.",
        )

    stream = streams[0]
    duration = _duration_from(data, stream)
    if duration <= 0:
        raise ValidationError(
            ErrorCode.CORRUPT_AUDIO,
            f"'{path.name}' süresi sıfır görünüyor.",
            details=f"biçim={data.get('format', {})}",
            suggestion="Dosya büyük ihtimalle eksik. Yeniden kaydedip tekrar deneyin.",
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
    path: Path, *, seconds: float = 4.0, settings: Settings | None = None
) -> list[float]:
    """Read presentation timestamps for the first ``seconds`` of video.

    Used to corroborate constant frame rate by measuring real intervals rather
    than trusting the container's advertised rate.

    The window is expressed in **time**, not frame count. ``-read_intervals``
    with a frame count (``%+#N``) stops on a packet boundary and its final
    samples skip frames, which makes a perfectly constant file look irregular.
    A time-based window cuts cleanly.
    """
    active = settings or get_settings()
    runner = FFmpegRunner(active)
    ffprobe = active.require_tool("ffprobe")
    result = runner._run_sync(  # noqa: SLF001 - internal helper, same module family
        [
            ffprobe, "-hide_banner", "-loglevel", "error",
            "-select_streams", "v:0",
            "-show_entries", "frame=pts_time",
            "-read_intervals", f"%+{seconds:g}",
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

    # ffprobe emits frames in *decode* order. H.264 with B-frames stores them
    # out of presentation order, so consecutive differences are only meaningful
    # once sorted — without this, a perfectly constant 60 fps file looks like it
    # has irregular intervals.
    timestamps.sort()
    return timestamps


#: Anything quieter than this is treated as silence rather than speech.
SILENCE_FLOOR_DB = -45.0
#: Shorter dips than this are pauses inside a sentence, not leading silence.
MIN_SILENCE_SECONDS = 0.08

_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")


#: A lead longer than this is not dead air at the head of a take, it is part of
#: the performance; leave it alone rather than pulling subtitles into it.
MAX_CREDIBLE_ONSET_SECONDS = 2.0


def measure_speech_onset(
    path: Path, *, duration: float | None = None, settings: Settings | None = None
) -> float:
    """How much silence an audio file opens with, before the first word.

    Used to place *estimated* subtitle cues. TTS output opens with roughly 0.15s
    of silence; laying cues across the raw file duration puts the first subtitle
    on screen before a word is spoken and carries that lead through the scene.

    Only the leading silence is reported. The silence at the *end* of a take is
    the natural sentence-final pause, which the cue estimator already accounts
    for through its punctuation weighting — trimming that too would double-count
    it and pull every cue early again.

    Returns 0.0 on any measurement problem: a worse cue baseline is always
    preferable to a failed render.
    """
    active = settings or get_settings()
    total = duration if duration is not None else probe_audio(path, settings=active).duration_seconds
    if total <= 0:
        return 0.0

    runner = FFmpegRunner(active)
    try:
        ffmpeg = active.require_tool("ffmpeg")
        result = runner._run_sync(  # noqa: SLF001 - same module family
            [
                ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
                "-af", f"silencedetect=noise={SILENCE_FLOOR_DB}dB:d={MIN_SILENCE_SECONDS}",
                "-f", "null", "-",
            ],
            timeout=120.0,
        )
    except AppError as exc:
        logger.info("could not measure the speech onset of %s: %s", path.name, exc)
        return 0.0

    starts = [float(v) for v in _SILENCE_START.findall(result.stderr)]
    ends = [float(v) for v in _SILENCE_END.findall(result.stderr)]

    # Only a silence that begins at the very top of the file is leading silence;
    # where it ends is where speech starts.
    if not ends or not starts or starts[0] > 0.05:
        return 0.0

    onset = min(ends[0], total)
    if onset <= 0.0 or onset > min(MAX_CREDIBLE_ONSET_SECONDS, total * 0.5):
        return 0.0
    return round(onset, 4)


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
