"""Encoder settings for intermediates, previews and final output.

Intermediate codec choice is a real trade-off between temp-disk usage, encode
time and generation loss. The defaults here were benchmarked on real
pan-and-zoom content rather than guessed; ``scripts/benchmark_codecs.py``
reproduces the measurement on any machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import IntermediateCodec, QualityPreset
from app.models.project import VideoSettings

#: Preview trades smoothness and frame rate for speed. The scene-clip stage
#: (supersampled ``zoompan`` at 60 fps) dominates render time and is otherwise
#: identical for every quality, so a "preview" that only cheapened the final
#: encode was barely faster than a full export. These caps make it genuinely
#: quick — full 1080p framing so subtitles and layout are still checkable, but
#: half the frames and a light supersample.
PREVIEW_FPS = 30
PREVIEW_SUPERSAMPLE = 1.5


@dataclass(frozen=True)
class EncoderSpec:
    """FFmpeg output arguments for one encoding target."""

    name: str
    args: list[str] = field(default_factory=list)
    suffix: str = ".mp4"
    description: str = ""
    #: Rough disk cost, MB per minute of 1080p60, from the benchmark.
    mb_per_minute: float = 0.0


@dataclass(frozen=True)
class RenderProfile:
    """The effective geometry one render uses, derived from the quality preset.

    Only ``PREVIEW`` departs from the project's own video settings: it renders at
    a lower frame rate and supersample factor so a rough check does not cost a
    full export. Every other quality mirrors the project exactly, so a real
    export is byte-for-byte what it was before this existed.

    ``cache_slug`` namespaces the per-scene clip cache. Preview clips are lighter
    and a different resolution/rate, so they live apart from the full-quality
    cache and neither one ever evicts the other — a quick preview never throws
    away the expensive clips a real export already built.
    """

    width: int
    height: int
    fps: int
    supersample: float
    cache_slug: str = ""


def render_profile(video: VideoSettings, quality: QualityPreset) -> RenderProfile:
    """Resolve the geometry for a render of ``quality``.

    Preview is capped *below* the project's settings (never above), so a project
    already configured for 30 fps or a light supersample keeps its own values.
    """
    if quality is QualityPreset.PREVIEW:
        return RenderProfile(
            width=video.width,
            height=video.height,
            fps=min(video.fps, PREVIEW_FPS),
            supersample=min(video.supersample_factor, PREVIEW_SUPERSAMPLE),
            cache_slug="preview",
        )
    return RenderProfile(
        width=video.width,
        height=video.height,
        fps=video.fps,
        supersample=video.supersample_factor,
        cache_slug="",
    )


#: Per-scene cached clips. These are decoded again during assembly, so they
#: trade a little disk for speed and quality.
INTERMEDIATE_SPECS: dict[IntermediateCodec, EncoderSpec] = {
    IntermediateCodec.H264_CRF14_FAST: EncoderSpec(
        name="h264-crf14-fast",
        args=[
            "-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-g", "60",
        ],
        suffix=".mp4",
        description="H.264 CRF 14, very fast. Small files, visually clean at one generation.",
        mb_per_minute=12.0,
    ),
    IntermediateCodec.H264_CRF12: EncoderSpec(
        name="h264-crf12",
        args=[
            "-c:v", "libx264", "-crf", "12", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-g", "60",
        ],
        suffix=".mp4",
        description="H.264 CRF 12, medium preset. Higher quality, slower, larger.",
        mb_per_minute=17.0,
    ),
    IntermediateCodec.PRORES_LT: EncoderSpec(
        name="prores-lt",
        args=["-c:v", "prores_ks", "-profile:v", "1", "-pix_fmt", "yuv422p10le"],
        suffix=".mov",
        description="ProRes 422 LT. Intra-frame, fast to decode, much larger files.",
        mb_per_minute=972.0,
    ),
    IntermediateCodec.PRORES_422: EncoderSpec(
        name="prores-422",
        args=["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"],
        suffix=".mov",
        description="ProRes 422. Highest intermediate quality, largest files.",
        mb_per_minute=1138.0,
    ),
}


#: Final delivery encodes.
QUALITY_SPECS: dict[QualityPreset, EncoderSpec] = {
    QualityPreset.PREVIEW: EncoderSpec(
        name="preview",
        args=[
            "-c:v", "libx264", "-crf", "26", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-profile:v", "main",
        ],
        description="Fast, low quality. For checking timing and layout only.",
        mb_per_minute=6.0,
    ),
    QualityPreset.STANDARD: EncoderSpec(
        name="standard",
        args=[
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
        ],
        description="Good quality, reasonable size and encode time.",
        mb_per_minute=11.0,
    ),
    QualityPreset.HIGH: EncoderSpec(
        name="high",
        args=[
            "-c:v", "libx264", "-crf", "18", "-preset", "slow",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-bf", "2",
        ],
        description="High quality. Slower encode.",
        mb_per_minute=13.0,
    ),
    QualityPreset.YOUTUBE_HQ: EncoderSpec(
        name="youtube-hq",
        args=[
            "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
            "-bf", "2", "-x264-params", "keyint=120:min-keyint=60",
        ],
        description=(
            "Tuned for YouTube's re-encode: extra quality headroom so the "
            "platform's own compression has good source material."
        ),
        mb_per_minute=17.0,
    ),
}


#: Hardware encoding is opt-in. VideoToolbox is much faster but gives worse
#: quality per bit than libx264, so it is never the default.
HARDWARE_SPECS: dict[QualityPreset, EncoderSpec] = {
    QualityPreset.PREVIEW: EncoderSpec(
        name="preview-hw",
        args=["-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p"],
        mb_per_minute=36.0,
    ),
    QualityPreset.STANDARD: EncoderSpec(
        name="standard-hw",
        args=["-c:v", "h264_videotoolbox", "-b:v", "12M", "-pix_fmt", "yuv420p"],
        mb_per_minute=55.0,
    ),
    QualityPreset.HIGH: EncoderSpec(
        name="high-hw",
        args=["-c:v", "h264_videotoolbox", "-b:v", "20M", "-pix_fmt", "yuv420p"],
        mb_per_minute=69.0,
    ),
    QualityPreset.YOUTUBE_HQ: EncoderSpec(
        name="youtube-hq-hw",
        args=["-c:v", "h264_videotoolbox", "-b:v", "28M", "-pix_fmt", "yuv420p"],
        mb_per_minute=87.0,
    ),
}


#: Final audio. 48 kHz stereo AAC is the safe universal choice.
AUDIO_ARGS: list[str] = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def intermediate_spec(codec: IntermediateCodec) -> EncoderSpec:
    return INTERMEDIATE_SPECS[codec]


def quality_spec(preset: QualityPreset, *, hardware: bool = False) -> EncoderSpec:
    if hardware and preset in HARDWARE_SPECS:
        return HARDWARE_SPECS[preset]
    return QUALITY_SPECS[preset]


def estimate_disk_mb(
    *,
    duration_seconds: float,
    scene_count: int,
    intermediate: IntermediateCodec,
    quality: QualityPreset,
    hardware: bool = False,
) -> dict[str, float]:
    """Estimate peak temporary and output disk usage for a render.

    Every second of video is written once as a scene clip and once as the final
    file. With H.264 intermediates both are small; with ProRes the intermediates
    dominate by roughly 60x, which is exactly why the preflight check exists.
    """
    minutes = duration_seconds / 60.0
    intermediate_mb = INTERMEDIATE_SPECS[intermediate].mb_per_minute * minutes
    output_mb = quality_spec(quality, hardware=hardware).mb_per_minute * minutes
    # Text cards, normalized images and the narration mix.
    assets_mb = scene_count * 3.0 + 25.0

    total = intermediate_mb + output_mb + assets_mb
    return {
        "intermediateMb": round(intermediate_mb, 1),
        "outputMb": round(output_mb, 1),
        "assetsMb": round(assets_mb, 1),
        "totalMb": round(total, 1),
    }
