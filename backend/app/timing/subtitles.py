"""Subtitle segmentation and timing.

Two rules drive everything here:

1. **Cue durations are never equal.** Time is distributed across cues by how
   long each one actually takes to say — word count, character length and the
   pauses that punctuation implies — then normalized so the total still equals
   the *measured* audio duration.
2. **Cues never split a protected phrase.** Scientific names and quoted phrases
   stay intact even when that makes a line slightly long.

When a provider supplies real word-level timings, those are used verbatim and
none of the estimation below runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.project import SubtitleStyle
from app.tts.base import WordTiming

logger = logging.getLogger("evb.subtitles")

#: Sentence-ending punctuation, kept with the sentence it closes.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")
#: Secondary split points, used only when a sentence is too long for one cue.
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")

#: Extra weight (in "virtual characters") for the pause each mark implies.
_PAUSE_WEIGHT = {".": 8.0, "!": 8.0, "?": 8.0, ";": 5.0, ":": 5.0, ",": 3.0, "—": 4.0}


@dataclass(frozen=True)
class Cue:
    """One subtitle, already wrapped into display lines."""

    index: int
    start_seconds: float
    end_seconds: float
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self.lines)


#: Words that frequently start an English sentence. Without this, "When sailors
#: approached" looks exactly like a binomial name to the pattern below.
_SENTENCE_STARTERS = frozenset(
    """the a an and but or so for yet when where while after before because if
    then than that this these those they there here it its his her their our your
    in on at by from with without within about over under during since until
    although though however therefore meanwhile today tomorrow yesterday
    one two three four five six seven eight nine ten some many most few all both
    each every no not only just even still also now soon later once twice
    modern ancient early late first second third final last next""".split()
)


def find_protected_phrases(text: str, extra: list[str] | None = None) -> list[str]:
    """Phrases that must never be split across two cues.

    Caller-supplied terms (the project's scientific name and pronunciation keys)
    are always honoured. Binomial names are additionally auto-detected, but
    conservatively: a false positive here glues ordinary words together and
    distorts line wrapping, so ambiguous matches are rejected.
    """
    protected: list[str] = [p for p in (extra or []) if p and p.strip()]

    # Binomial nomenclature: capitalised genus + lowercase species. Require a
    # genus of 4+ letters and reject anything whose first word is a common
    # sentence opener, which is the dominant source of false matches.
    for match in re.finditer(r"\b([A-Z][a-z]{3,})\s+([a-z]{4,})\b", text):
        genus, species = match.group(1), match.group(2)
        if genus.lower() in _SENTENCE_STARTERS or species in _SENTENCE_STARTERS:
            continue
        protected.append(match.group(0))

    # Quoted phrases of two or more words.
    protected.extend(re.findall(r"[\"“]([^\"”]{3,60})[\"”]", text))

    # Longest first so the most specific phrase wins.
    return sorted({p.strip() for p in protected if len(p.split()) > 1}, key=len, reverse=True)


def split_into_segments(text: str, style: SubtitleStyle, protected: list[str]) -> list[str]:
    """Break narration into cue-sized chunks at natural boundaries."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    budget = style.max_chars_per_line * style.max_lines
    segments: list[str] = []

    for sentence in _SENTENCE_END.split(normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= budget:
            segments.append(sentence)
            continue

        # Too long for one cue: split on clause punctuation, then pack greedily.
        clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip()]
        buffer = ""
        for clause in clauses:
            candidate = f"{buffer} {clause}".strip()
            if buffer and len(candidate) > budget:
                segments.append(buffer)
                buffer = clause
            else:
                buffer = candidate
        if buffer:
            segments.append(buffer)

    # A single clause can still exceed the budget; break it on word boundaries
    # without ever cutting a protected phrase.
    final: list[str] = []
    for segment in segments:
        final.extend(_split_long(segment, budget, protected) if len(segment) > budget else [segment])
    return final


def _split_long(segment: str, budget: int, protected: list[str]) -> list[str]:
    """Word-wrap an over-long segment, keeping protected phrases whole."""
    tokens = _tokenize_protected(segment, protected)
    chunks: list[str] = []
    buffer = ""
    for token in tokens:
        candidate = _join([buffer, token]) if buffer else token
        if buffer and len(candidate) > budget:
            chunks.append(buffer)
            buffer = token
        else:
            buffer = candidate
    if buffer:
        chunks.append(buffer)
    return chunks


#: Punctuation that must never be preceded by a space after a token rejoin.
_ORPHAN_PUNCTUATION = re.compile(r"\s+([,.;:!?%)\]}»”’])")


def _join(tokens: list[str]) -> str:
    """Join tokens with spaces, without stranding punctuation.

    Tokenizing around a protected phrase can leave a trailing comma as its own
    token; a naive join would render "approached , the".
    """
    return _ORPHAN_PUNCTUATION.sub(r"\1", " ".join(tokens)).strip()


def _tokenize_protected(text: str, protected: list[str]) -> list[str]:
    """Split into words, but treat each protected phrase as one token."""
    if not protected:
        return text.split()

    pattern = "|".join(re.escape(p) for p in protected)
    tokens: list[str] = []
    position = 0
    for match in re.finditer(pattern, text, re.IGNORECASE):
        # Only accept the match if it does not start mid-token.
        tokens.extend(text[position : match.start()].split())
        tokens.append(match.group(0))
        position = match.end()
    tokens.extend(text[position:].split())
    return [t for t in tokens if t]


def wrap_lines(segment: str, style: SubtitleStyle, protected: list[str]) -> list[str]:
    """Wrap one cue into at most ``max_lines`` display lines."""
    tokens = _tokenize_protected(segment, protected)
    lines: list[str] = []
    buffer = ""
    for token in tokens:
        candidate = _join([buffer, token]) if buffer else token
        if buffer and len(candidate) > style.max_chars_per_line:
            lines.append(buffer)
            buffer = token
        else:
            buffer = candidate
    if buffer:
        lines.append(buffer)

    if len(lines) > style.max_lines:
        # Merge the overflow into the last allowed line rather than dropping text.
        head = lines[: style.max_lines - 1]
        head.append(" ".join(lines[style.max_lines - 1 :]))
        lines = head
    return lines


def speech_weight(segment: str) -> float:
    """How long this segment takes to say, in arbitrary comparable units.

    Character count is the base signal; word count adds the cost of articulation
    boundaries, and punctuation adds the pause it implies. This is what makes
    cue durations proportional to real speech rather than uniform.
    """
    characters = float(len(segment))
    words = float(len(segment.split()))
    pause = sum(_PAUSE_WEIGHT.get(char, 0.0) for char in segment)
    return characters + words * 2.0 + pause


def build_cues(
    text: str,
    *,
    total_duration: float,
    style: SubtitleStyle,
    start_offset: float = 0.0,
    protected_extra: list[str] | None = None,
    word_timings: list[WordTiming] | None = None,
    start_index: int = 1,
) -> list[Cue]:
    """Turn narration plus its measured duration into timed cues."""
    if total_duration <= 0:
        return []

    protected = find_protected_phrases(text, protected_extra)
    segments = split_into_segments(text, style, protected)
    if not segments:
        return []

    if word_timings:
        spans = _align_to_word_timings(segments, word_timings)
    else:
        spans = _distribute_by_weight(segments, total_duration, style)

    cues: list[Cue] = []
    for index, (segment, (start, end)) in enumerate(zip(segments, spans, strict=True)):
        cues.append(
            Cue(
                index=start_index + index,
                start_seconds=round(start_offset + start, 3),
                end_seconds=round(start_offset + end, 3),
                lines=wrap_lines(segment, style, protected),
            )
        )
    return _enforce_ordering(cues)


def _distribute_by_weight(
    segments: list[str], total: float, style: SubtitleStyle
) -> list[tuple[float, float]]:
    """Split ``total`` across segments by speech weight, honouring cue bounds.

    When the clamped cues do not fill the audio, the leftover time becomes
    *gaps between cues* rather than stretching each cue. That is how real
    subtitles behave: a four-word line does not sit on screen for eleven
    seconds just because the narration around it is slow.
    """
    weights = [max(speech_weight(s), 1.0) for s in segments]
    weight_sum = sum(weights)
    durations = [total * w / weight_sum for w in weights]

    # Readability bounds: long enough to read, short enough not to linger.
    for index, segment in enumerate(segments):
        minimum = max(style.min_cue_seconds, len(segment) / style.max_chars_per_second)
        durations[index] = min(max(durations[index], minimum), style.max_cue_seconds)

    occupied = sum(durations)

    if occupied > total:
        # The text cannot be read comfortably in the time available. Compress
        # proportionally: overrunning the audio would be worse than being fast.
        scale = total / occupied
        durations = [d * scale for d in durations]
        gaps = [0.0] * len(durations)
    elif len(durations) == 1:
        # Nowhere to put a gap; a lone cue covers the whole narration.
        durations = [total]
        gaps = [0.0]
    else:
        # Spread the slack between cues, weighted the same way, so the final cue
        # still ends exactly when the narration does.
        slack = total - occupied
        gap_weights = weights[:-1]
        gap_total = sum(gap_weights)
        gaps = [slack * w / gap_total for w in gap_weights] + [0.0]

    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for duration, gap in zip(durations, gaps, strict=True):
        spans.append((cursor, cursor + duration))
        cursor += duration + gap
    return spans


def _comparable(text: str) -> str:
    """Letters and digits only, lowercased — for matching text across sources."""
    return re.sub(r"[^\w]", "", text).lower()


def _align_to_word_timings(
    segments: list[str], word_timings: list[WordTiming]
) -> list[tuple[float, float]]:
    """Use real provider timings, matching by text coverage.

    Providers differ in granularity: Edge emits word boundaries when asked and
    sentence boundaries by default. Consuming entries until their combined text
    covers the segment works for both, and tolerates punctuation being attached
    differently on each side.
    """
    spans: list[tuple[float, float]] = []
    cursor = 0

    for segment in segments:
        target = _comparable(segment)
        if not target or cursor >= len(word_timings):
            last = spans[-1][1] if spans else 0.0
            spans.append((last, last + 1.0))
            continue

        start = word_timings[cursor].start_seconds
        end = word_timings[cursor].end_seconds
        accumulated = ""

        while cursor < len(word_timings) and len(accumulated) < len(target):
            timing = word_timings[cursor]
            accumulated += _comparable(timing.word)
            end = timing.end_seconds
            cursor += 1

        spans.append((start, max(end, start + 0.3)))

    return spans


def _enforce_ordering(cues: list[Cue]) -> list[Cue]:
    """Guarantee strictly increasing, non-overlapping, non-zero ranges.

    An invalid SRT range makes players drop the file entirely, so this is a
    hard postcondition rather than a nicety.
    """
    fixed: list[Cue] = []
    previous_end = -1.0
    for cue in cues:
        start = max(cue.start_seconds, previous_end + 0.001)
        end = max(cue.end_seconds, start + 0.3)
        fixed.append(Cue(index=cue.index, start_seconds=round(start, 3),
                         end_seconds=round(end, 3), lines=cue.lines))
        previous_end = end
    return fixed


def format_timestamp(seconds: float) -> str:
    """SRT timestamp: ``HH:MM:SS,mmm``."""
    if seconds < 0:
        seconds = 0.0
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def render_srt(cues: list[Cue], *, renumber: bool = True) -> str:
    """Serialize cues as an SRT document."""
    blocks: list[str] = []
    for position, cue in enumerate(cues, start=1):
        index = position if renumber else cue.index
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(cue.start_seconds)} --> {format_timestamp(cue.end_seconds)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def validate_cues(cues: list[Cue]) -> list[str]:
    """Return a list of problems. Empty means the cue list is valid SRT."""
    problems: list[str] = []
    previous_end = -1.0
    for cue in cues:
        if cue.end_seconds <= cue.start_seconds:
            problems.append(f"cue {cue.index}: end ({cue.end_seconds}) is not after start ({cue.start_seconds})")
        if cue.start_seconds < previous_end:
            problems.append(f"cue {cue.index}: overlaps the previous cue")
        if not cue.text.strip():
            problems.append(f"cue {cue.index}: empty text")
        previous_end = cue.end_seconds
    return problems
