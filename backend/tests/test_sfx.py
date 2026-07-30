from __future__ import annotations

import struct
import wave
from pathlib import Path

from app.models.project import LongIntro, ShortHook
from app.render.sfx import SAMPLE_RATE, build_long_intro_sfx, build_short_hook_sfx


def _wav_info(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
        peak = max(abs(sample[0]) for sample in struct.iter_unpack("<h", frames))
        return audio.getframerate(), audio.getnchannels(), peak


def test_long_intro_synthesizes_a_cached_stereo_typewriter_and_stamp(tmp_path: Path) -> None:
    intro = LongIntro(
        primary_title="Dodo",
        secondary_title="Raphus cucullatus",
    )
    first = build_long_intro_sfx(intro, output_dir=tmp_path)
    second = build_long_intro_sfx(intro, output_dir=tmp_path)
    assert first is not None
    assert second == first
    rate, channels, peak = _wav_info(first)
    assert rate == SAMPLE_RATE
    assert channels == 2
    assert peak > 1_000


def test_plain_long_intro_has_no_fake_typewriter_sound(tmp_path: Path) -> None:
    intro = LongIntro(
        intro_style="plain-title",  # type: ignore[arg-type]
        primary_title="Dodo",
    )
    assert build_long_intro_sfx(intro, output_dir=tmp_path) is None


def test_short_hook_synthesizes_a_rise_and_impact_without_filling_the_whole_short(
    tmp_path: Path,
) -> None:
    hook = ShortHook(lines=["When he died,", "the species ended"])
    path = build_short_hook_sfx(
        hook,
        impact_at=0.42,
        total_duration_seconds=120.0,
        output_dir=tmp_path,
    )
    assert path is not None
    rate, channels, peak = _wav_info(path)
    assert rate == SAMPLE_RATE
    assert channels == 2
    assert peak > 1_000
    with wave.open(str(path), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() < 3.0
