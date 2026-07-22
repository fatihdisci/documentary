"""Word timings, kept on disk beside the audio they describe.

Edge TTS is asked for word-level boundaries precisely so subtitles land on the
words rather than on an estimate. Those timings used to live only in the memory
of the process that synthesized the audio, so the moment the audio came back
from the cache — which is the normal case, because narration is generated on the
Audio tab and the render happens later — every cue fell back to being estimated
from character counts. Measured against the real boundaries, that estimate runs
up to 0.66s ahead of the words.

Persisting them next to the audio fixes that: the timings are cached by exactly
the same content hash as the audio file, so they can never describe a different
take than the one being played.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.tts.base import WordTiming

logger = logging.getLogger("evb.tts.timings")

#: Bumped if the stored shape changes. An unreadable or unknown file is simply
#: ignored, which degrades to the estimator rather than failing a render.
TIMINGS_SCHEMA_VERSION = 1

SUFFIX = ".timings.json"


def timings_path_for(audio_path: Path) -> Path:
    """Where the timings for ``audio_path`` live. Always beside the audio."""
    return audio_path.with_suffix(SUFFIX)


def save_word_timings(audio_path: Path, timings: list[WordTiming]) -> Path | None:
    """Write timings beside the audio. Returns None when there are none."""
    if not timings:
        return None
    target = timings_path_for(audio_path)
    payload = {
        "schemaVersion": TIMINGS_SCHEMA_VERSION,
        "audioFile": audio_path.name,
        "words": [
            {
                "word": timing.word,
                "startSeconds": round(timing.start_seconds, 4),
                "endSeconds": round(timing.end_seconds, 4),
            }
            for timing in timings
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), "utf-8")
    tmp.replace(target)
    return target


def load_word_timings(audio_path: Path) -> list[WordTiming]:
    """Read timings for ``audio_path``, or return an empty list.

    Never raises: a missing, corrupt or newer-schema file just means the cue
    estimator is used instead, which is a worse subtitle but a working render.
    """
    path = timings_path_for(audio_path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("ignoring unreadable word timings %s: %s", path.name, exc)
        return []

    if raw.get("schemaVersion") != TIMINGS_SCHEMA_VERSION:
        logger.info("ignoring word timings %s written by another version", path.name)
        return []
    if raw.get("audioFile") not in (None, audio_path.name):
        logger.warning("word timings %s describe a different audio file", path.name)
        return []

    timings: list[WordTiming] = []
    for entry in raw.get("words") or []:
        try:
            timings.append(
                WordTiming(
                    word=str(entry["word"]),
                    start_seconds=float(entry["startSeconds"]),
                    end_seconds=float(entry["endSeconds"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping malformed word timing in %s", path.name)
            return []
    return timings
