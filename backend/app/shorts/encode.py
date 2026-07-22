"""Encoder settings for Shorts.

Kept apart from the planner and the pipeline because both need them: the planner
folds them into the cache key (a different encoder means a different file, so it
must invalidate), and the pipeline actually runs them.

Everything here is an argument **list**. No string is ever handed to a shell, and
nothing user-supplied appears in a filtergraph.
"""

from __future__ import annotations

import hashlib
import json

from app.models.enums import QualityPreset
from app.render.codecs import AUDIO_ARGS, quality_spec

#: Bumped when the FFmpeg graph itself changes shape. Invalidates every cached
#: Short, because the same request would now produce different pixels.
SHORTS_PIPELINE_VERSION = 1

#: Final audio: 48 kHz stereo AAC, the same universally safe choice the long
#: pipeline makes.
SHORT_AUDIO_ARGS: list[str] = list(AUDIO_ARGS)

#: Intermediate cut segments. Near-lossless and fast: they are decoded once more
#: during the compose pass, so one visually transparent generation is the whole
#: cost of being frame-accurate.
SEGMENT_VIDEO_ARGS: list[str] = [
    "-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
    "-pix_fmt", "yuv420p",
]


def short_video_args() -> list[str]:
    """Delivery encode for the finished Short.

    Always software libx264: a Short is at most three minutes, the encode is
    seconds either way, and a deterministic encoder keeps the content-addressed
    cache honest across machines with and without VideoToolbox.
    """
    return list(quality_spec(QualityPreset.YOUTUBE_HQ).args)


def segment_cache_name(source_sha256: str, start: float, end: float, fps: int) -> str:
    """Content-addressed filename for one cached cut.

    Derived from the source checksum, the exact span and the encoder settings,
    so a cut can never be reused across a source that has changed underneath it.
    Shared by the pipeline (which writes them) and the delete path (which must
    know which ones belong to a Short).
    """
    digest = hashlib.sha256(
        "|".join(
            [
                source_sha256,
                f"{start:.4f}",
                f"{end:.4f}",
                str(fps),
                ",".join(SEGMENT_VIDEO_ARGS),
                ",".join(SHORT_AUDIO_ARGS),
            ]
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"cut-{digest}.mp4"


def encoder_fingerprint(fps: int) -> str:
    """Stable digest of everything about the encode that affects the output."""
    payload = json.dumps(
        {
            "version": SHORTS_PIPELINE_VERSION,
            "fps": fps,
            "segment": SEGMENT_VIDEO_ARGS,
            "video": short_video_args(),
            "audio": SHORT_AUDIO_ARGS,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
