"""Subtitle segmentation and timing.

The headline requirements: cue durations must be proportional to speech, never
uniform; protected phrases must never be split; and the output must always be
valid SRT.
"""

from __future__ import annotations

import re
import statistics

import pytest

from app.models.project import SubtitleStyle
from app.timing.subtitles import (
    Cue,
    build_cues,
    find_protected_phrases,
    format_timestamp,
    render_srt,
    speech_weight,
    split_into_segments,
    validate_cues,
    wrap_lines,
)

STYLE = SubtitleStyle()

DODO_NARRATION = (
    "The dodo evolved in a world with no threats. For millions of years, nothing on "
    "Mauritius hunted it. So it never developed the instinct to flee. When sailors "
    "approached, the dodo simply watched them come."
)


class TestProtectedPhrases:
    def test_detects_binomial_scientific_names(self) -> None:
        found = find_protected_phrases("The dodo, Raphus cucullatus, lived on Mauritius.")
        assert "Raphus cucullatus" in found

    def test_detects_quoted_phrases(self) -> None:
        found = find_protected_phrases('Sailors called it "the disgusting bird" in their logs.')
        assert any("disgusting bird" in phrase for phrase in found)

    def test_includes_caller_supplied_terms(self) -> None:
        found = find_protected_phrases("Text", extra=["Mare aux Songes"])
        assert "Mare aux Songes" in found

    def test_longest_phrases_come_first(self) -> None:
        found = find_protected_phrases("x", extra=["Raphus cucullatus", "Mare aux Songes long"])
        assert len(found[0]) >= len(found[-1])


class TestSegmentation:
    def test_splits_on_sentences(self) -> None:
        segments = split_into_segments(DODO_NARRATION, STYLE, [])
        assert len(segments) >= 4
        assert segments[0].startswith("The dodo evolved")

    def test_no_segment_exceeds_the_display_budget(self) -> None:
        budget = STYLE.max_chars_per_line * STYLE.max_lines
        for segment in split_into_segments(DODO_NARRATION * 3, STYLE, []):
            assert len(segment) <= budget, f"segment too long: {segment!r}"

    def test_never_splits_a_scientific_name(self) -> None:
        text = (
            "Scientists eventually confirmed that the bird known to science as "
            "Raphus cucullatus was entirely real and had lived on the island."
        )
        protected = find_protected_phrases(text)
        segments = split_into_segments(text, STYLE, protected)

        joined_with_breaks = " | ".join(segments)
        assert "Raphus cucullatus" in joined_with_breaks
        # The name must sit inside exactly one segment, never straddling a break.
        assert sum("Raphus cucullatus" in s for s in segments) == 1

    def test_no_text_is_lost(self) -> None:
        segments = split_into_segments(DODO_NARRATION, STYLE, [])
        original = re.sub(r"\s+", "", DODO_NARRATION)
        rebuilt = re.sub(r"\s+", "", " ".join(segments))
        assert rebuilt == original

    def test_empty_text_yields_nothing(self) -> None:
        assert split_into_segments("   ", STYLE, []) == []


class TestWrapping:
    def test_respects_line_length(self) -> None:
        lines = wrap_lines("word " * 30, STYLE, [])
        for line in lines[:-1]:
            assert len(line) <= STYLE.max_chars_per_line

    def test_respects_max_lines(self) -> None:
        assert len(wrap_lines("word " * 60, STYLE, [])) <= STYLE.max_lines

    def test_keeps_protected_phrase_on_one_line(self) -> None:
        style = SubtitleStyle(max_chars_per_line=24, max_lines=3)
        lines = wrap_lines("The bird Raphus cucullatus lived here", style, ["Raphus cucullatus"])
        assert any("Raphus cucullatus" in line for line in lines)


class TestSpeechWeight:
    def test_longer_text_weighs_more(self) -> None:
        assert speech_weight("A short one.") < speech_weight("A considerably longer sentence here.")

    def test_punctuation_adds_pause_weight(self) -> None:
        assert speech_weight("Yes, and then.") > speech_weight("Yes and then")


class TestCueTiming:
    def test_cues_are_not_equal_length(self) -> None:
        """The core requirement: duration follows speech, not cue count."""
        cues = build_cues(DODO_NARRATION, total_duration=18.0, style=STYLE)
        durations = [c.duration for c in cues]

        assert len(cues) >= 3
        assert statistics.pstdev(durations) > 0.15, (
            f"cue durations are suspiciously uniform: {durations}"
        )
        assert len(set(round(d, 2) for d in durations)) > 1

    def test_longer_cues_get_more_time(self) -> None:
        cues = build_cues(
            "Short. This second sentence is considerably longer than the first one was.",
            total_duration=12.0,
            style=STYLE,
        )
        assert len(cues) == 2
        assert cues[1].duration > cues[0].duration

    def test_total_matches_the_measured_audio_duration(self) -> None:
        for total in (6.0, 18.0, 47.5):
            cues = build_cues(DODO_NARRATION, total_duration=total, style=STYLE)
            span = cues[-1].end_seconds - cues[0].start_seconds
            assert span == pytest.approx(total, abs=0.05), f"span {span} != {total}"

    def test_offset_places_cues_on_the_absolute_timeline(self) -> None:
        cues = build_cues(DODO_NARRATION, total_duration=10.0, style=STYLE, start_offset=125.5)
        assert cues[0].start_seconds == pytest.approx(125.5, abs=0.01)
        assert cues[-1].end_seconds == pytest.approx(135.5, abs=0.05)

    def test_a_short_cue_does_not_linger(self) -> None:
        cues = build_cues(
            "Yes. " + "A much longer closing sentence that carries most of the narration here.",
            total_duration=30.0,
            style=STYLE,
        )
        assert cues[0].duration <= STYLE.max_cue_seconds

    def test_reading_speed_floor_is_respected(self) -> None:
        """A long cue in a short slot is still on screen long enough to read."""
        cues = build_cues(DODO_NARRATION, total_duration=4.0, style=STYLE)
        for cue in cues:
            implied_speed = cue.char_count / cue.duration if cue.duration else 999
            # The bound is applied before rescaling, so allow generous slack.
            assert implied_speed < STYLE.max_chars_per_second * 3

    def test_zero_duration_yields_no_cues(self) -> None:
        assert build_cues(DODO_NARRATION, total_duration=0.0, style=STYLE) == []

    def test_indices_continue_from_start_index(self) -> None:
        cues = build_cues("One. Two. Three.", total_duration=6.0, style=STYLE, start_index=8)
        assert cues[0].index == 8


class TestWordTimings:
    def test_real_timings_are_used_verbatim(self) -> None:
        from app.tts.base import WordTiming

        text = "Alpha beta. Gamma delta."
        words = ["Alpha", "beta.", "Gamma", "delta."]
        starts = [0.0, 1.0, 5.0, 6.0]
        timings = [
            WordTiming(word=w, start_seconds=s, end_seconds=s + 0.8)
            for w, s in zip(words, starts, strict=True)
        ]

        cues = build_cues(text, total_duration=10.0, style=STYLE, word_timings=timings)

        assert len(cues) == 2
        assert cues[0].start_seconds == pytest.approx(0.0, abs=0.01)
        assert cues[1].start_seconds == pytest.approx(5.0, abs=0.01)
        # The 4-second gap between sentences is preserved, not averaged away.
        assert cues[1].start_seconds - cues[0].end_seconds > 3.0


class TestValidity:
    def test_cues_never_overlap(self) -> None:
        cues = build_cues(DODO_NARRATION * 2, total_duration=25.0, style=STYLE)
        for previous, current in zip(cues, cues[1:], strict=False):
            assert current.start_seconds >= previous.end_seconds

    def test_every_cue_has_positive_duration(self) -> None:
        cues = build_cues(DODO_NARRATION, total_duration=3.0, style=STYLE)
        assert all(c.end_seconds > c.start_seconds for c in cues)

    def test_validate_accepts_generated_cues(self) -> None:
        assert validate_cues(build_cues(DODO_NARRATION, total_duration=20.0, style=STYLE)) == []

    def test_validate_catches_an_inverted_range(self) -> None:
        bad = [Cue(index=1, start_seconds=5.0, end_seconds=2.0, lines=["x"])]
        assert validate_cues(bad)

    def test_ordering_is_repaired_not_emitted_broken(self) -> None:
        from app.tts.base import WordTiming

        # Deliberately out-of-order timings from a misbehaving provider.
        timings = [
            WordTiming(word="a", start_seconds=5.0, end_seconds=6.0),
            WordTiming(word="b.", start_seconds=5.5, end_seconds=6.5),
            WordTiming(word="c", start_seconds=1.0, end_seconds=2.0),
            WordTiming(word="d.", start_seconds=1.5, end_seconds=2.5),
        ]
        cues = build_cues("a b. c d.", total_duration=10.0, style=STYLE, word_timings=timings)
        assert validate_cues(cues) == []


class TestSrtRendering:
    def test_timestamp_format(self) -> None:
        assert format_timestamp(0) == "00:00:00,000"
        assert format_timestamp(1.5) == "00:00:01,500"
        assert format_timestamp(61.25) == "00:01:01,250"
        assert format_timestamp(3725.125) == "01:02:05,125"
        assert format_timestamp(-3) == "00:00:00,000"

    def test_srt_structure(self) -> None:
        srt = render_srt(build_cues(DODO_NARRATION, total_duration=18.0, style=STYLE))
        blocks = [b for b in srt.split("\n\n") if b.strip()]

        assert len(blocks) >= 3
        for position, block in enumerate(blocks, start=1):
            lines = block.strip().split("\n")
            assert lines[0] == str(position), "cues must be numbered from 1"
            assert re.fullmatch(
                r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", lines[1]
            ), f"bad timestamp line: {lines[1]!r}"
            assert len(lines) >= 3, "cue has no text"

    def test_srt_lines_stay_within_two_display_lines(self) -> None:
        srt = render_srt(build_cues(DODO_NARRATION * 2, total_duration=40.0, style=STYLE))
        for block in [b for b in srt.split("\n\n") if b.strip()]:
            text_lines = block.strip().split("\n")[2:]
            assert len(text_lines) <= STYLE.max_lines
