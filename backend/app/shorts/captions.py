"""Drawing Shorts-native captions.

Same machinery as every other piece of text in this app: Pillow draws an RGBA
PNG and FFmpeg composites it with ``overlay``. There is no ``drawtext`` here, no
``subtitles`` filter and no libass — this FFmpeg build has none of them, and the
Pillow path is better anyway (bundled fonts, identical output everywhere,
rounded boxes, real multi-line control).

The one thing this module adds over ``render/text.py`` is **fitting**. A cue was
segmented for a 1920-wide frame at 38 px, where two lines of 42 characters fit
comfortably. The same words at 58 px on a 1080-wide canvas want four lines. So
before anything is drawn, one type size is chosen that fits *every* cue in the
Short within ``max_lines`` — one size for the whole clip, because captions that
change size cue to cue look broken.

Placement is in the 9:16 canvas' own coordinates, below the letterboxed picture,
so the caption is large and fixed in Shorts space rather than scaled down with
the film.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.models.enums import TextPosition
from app.models.project import TextStyle
from app.render import fonts
from app.render.text import TextCard, render_card
from app.shorts.cues import RebasedCue
from app.shorts.models import ShortCaptionStyle

logger = logging.getLogger("evb.shorts.captions")

#: Bumped when anything here changes the pixels a caption produces. Folded into
#: the cache key, so a cached Short is never served from a different renderer.
CAPTION_RENDERER_VERSION = 1

#: Above this many caption overlays, they are pre-composited into one alpha track
#: instead of being chained into the compose filtergraph. Mirrors the long
#: pipeline's own threshold in ``render/scene_clip.py`` for the same reason: a
#: dense track would otherwise produce an enormous filtergraph.
MAX_INLINE_CAPTION_OVERLAYS = 12

#: Steps the fitter walks down through, in points.
_FIT_STEP = 2


@dataclass(frozen=True)
class CaptionCard:
    """One drawn caption and the window it is visible for."""

    cue: RebasedCue
    card: TextCard
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass
class CaptionTrack:
    """Everything the pipeline needs to composite a Short's captions."""

    cards: list[CaptionCard] = field(default_factory=list)
    fitted_font_size: int = 0
    #: Set when the cards were baked into one transparent video instead.
    precomposed: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not self.cards

    @property
    def should_precompose(self) -> bool:
        return len(self.cards) > MAX_INLINE_CAPTION_OVERLAYS


def as_text_style(style: ShortCaptionStyle, *, size: int | None = None) -> TextStyle:
    """Project a caption style onto the ``TextStyle`` ``render_card`` speaks.

    A translation, not a second style system: every value is carried across
    unchanged so a caption is drawn by exactly the code that draws titles.
    """
    return TextStyle(
        font_family=style.font_family,
        font_weight=style.font_weight,
        size=size if size is not None else style.font_size,
        color=style.color,
        letter_spacing=style.letter_spacing,
        line_spacing=style.line_spacing,
        shadow=style.shadow,
        shadow_blur=style.shadow_blur,
        shadow_offset=style.shadow_offset,
        outline_width=style.outline_width,
        outline_color=style.outline_color,
        box=style.box,
        box_color=style.box_color,
        box_opacity=style.box_opacity,
        box_padding_x=style.box_padding_x,
        box_padding_y=style.box_padding_y,
        box_radius=style.box_radius,
        max_width_ratio=style.max_width_ratio,
        # Fading is applied by the compositor over time, not baked into the card.
        fade_in_seconds=0.0,
        fade_out_seconds=0.0,
    )


def _wrap_to_width(text: str, font, max_width: float, letter_spacing: float) -> list[str]:
    """Greedy word wrap by measured pixel width, ignoring existing line breaks."""
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    buffer = ""
    for word in words:
        candidate = f"{buffer} {word}".strip()
        width = font.getlength(candidate) + max(0, len(candidate) - 1) * letter_spacing
        if buffer and width > max_width:
            lines.append(buffer)
            buffer = word
        else:
            buffer = candidate
    if buffer:
        lines.append(buffer)
    return lines or [""]


def fit_caption_typography(
    texts: list[str], style: ShortCaptionStyle, *, canvas_width: int
) -> tuple[int, dict[str, list[str]]]:
    """Pick one type size that fits every cue within ``max_lines``.

    Returns the size and the wrapped lines for each distinct text. Walking down
    from the nominal size in small steps is deliberate: the first size that fits
    is the largest one that does, and stopping at ``min_font_scale`` means a
    single pathologically long cue makes captions slightly smaller rather than
    illegible. If even the floor cannot fit a cue, its overflow is merged into
    the last allowed line — the same compromise ``timing/subtitles.wrap_lines``
    makes, and for the same reason: never silently drop words.
    """
    unique = [text for text in dict.fromkeys(texts) if text.strip()]
    if not unique:
        return style.font_size, {}

    floor = max(12, int(round(style.font_size * style.min_font_scale)))
    available = canvas_width * style.max_width_ratio - 2 * style.box_padding_x

    size = style.font_size
    while size >= floor:
        font = fonts.load(style.font_family, style.font_weight, size)
        wrapped = {
            text: _wrap_to_width(text, font, available, style.letter_spacing)
            for text in unique
        }
        if all(len(lines) <= style.max_lines for lines in wrapped.values()):
            return size, wrapped
        size -= _FIT_STEP

    font = fonts.load(style.font_family, style.font_weight, floor)
    wrapped = {}
    for text in unique:
        lines = _wrap_to_width(text, font, available, style.letter_spacing)
        if len(lines) > style.max_lines:
            head = lines[: style.max_lines - 1]
            head.append(" ".join(lines[style.max_lines - 1 :]))
            lines = head
        wrapped[text] = lines
    logger.info(
        "caption type fitted down to the %dpx floor for %d cue(s)", floor, len(unique)
    )
    return floor, wrapped


def build_caption_track(
    cues: list[RebasedCue],
    style: ShortCaptionStyle,
    *,
    canvas_width: int,
    canvas_height: int,
    output_dir: Path,
) -> CaptionTrack:
    """Draw a card for every cue, placed on the vertical canvas.

    Placement uses ``safe_bottom_inset`` as the margin for a bottom-centre
    placement, so the *bottom of the box* sits exactly that many pixels above the
    bottom of the canvas whatever the type size turns out to be.
    """
    if not cues:
        return CaptionTrack()

    fitted, wrapped = fit_caption_typography(
        [cue.text for cue in cues], style, canvas_width=canvas_width
    )
    text_style = as_text_style(style, size=fitted)

    cards: list[CaptionCard] = []
    for cue in cues:
        lines = wrapped.get(cue.text)
        if not lines:
            continue
        # The card is handed pre-wrapped text. ``render_card`` honours explicit
        # newlines and only re-wraps a line that is still too wide, which by
        # construction none of these are.
        card = render_card(
            "\n".join(lines),
            text_style,
            frame_width=canvas_width,
            frame_height=canvas_height,
            position=TextPosition.BOTTOM_CENTER,
            margin=style.safe_bottom_inset,
            output_dir=output_dir,
        )
        if card is None:
            continue
        cards.append(
            CaptionCard(
                cue=cue,
                card=card,
                start_seconds=cue.start_seconds,
                end_seconds=cue.end_seconds,
            )
        )

    logger.info(
        "built %d caption card(s) at %dpx for a %dx%d canvas",
        len(cards), fitted, canvas_width, canvas_height,
    )
    return CaptionTrack(cards=cards, fitted_font_size=fitted)


def track_digest(track: CaptionTrack, *, canvas_width: int, canvas_height: int) -> str:
    """Content address for one pre-composited caption track."""
    payload = "\x1f".join(
        [
            str(CAPTION_RENDERER_VERSION),
            f"{canvas_width}x{canvas_height}",
            str(track.fitted_font_size),
            *(
                f"{card.start_seconds:.3f}-{card.end_seconds:.3f}"
                f"@{card.card.x},{card.card.y}:{card.card.path.name}"
                for card in track.cards
            ),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def overlay_steps(
    track: CaptionTrack,
    *,
    style: ShortCaptionStyle,
    current: str,
    add_input,  # noqa: ANN001 - callable returning the new input's index
    duration: float,
) -> tuple[list[str], str]:
    """Filter steps chaining every caption card onto ``current``.

    Every value interpolated below is an integer or a rounded float this module
    computed from validated model fields. Caption *text* only ever exists as
    pixels in a PNG — it never appears in a filtergraph.

    Cues that genuinely overlap (two scenes dissolving into each other, each with
    its own caption) are chained in order, so the later one alpha-composites over
    the earlier one exactly as their pictures do.
    """
    steps: list[str] = []
    for number, entry in enumerate(track.cards):
        start = max(0.0, entry.start_seconds)
        end = min(duration, entry.end_seconds)
        if end <= start:
            continue
        index = add_input(entry.card.path)
        fade = min(style.fade_seconds, max(0.0, (end - start) / 3))
        label = f"cap{number}"
        if fade > 0:
            steps.append(
                f"[{index}:v]format=rgba,"
                f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
                f"fade=t=out:st={max(start, end - fade):.3f}:d={fade:.3f}:alpha=1[{label}]"
            )
        else:
            steps.append(f"[{index}:v]format=rgba[{label}]")
        steps.append(
            f"[{current}][{label}]overlay={entry.card.x}:{entry.card.y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[with{label}]"
        )
        current = f"with{label}"
    return steps, current
