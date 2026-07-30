"""The branded long-video opening.

Every video on the channel starts the same way: the animal's name types itself
out, the scientific name fades in small underneath, and a red ``EXTINCT`` stamp
lands over both. Two and a half seconds, then it dissolves into the film.

**It is an overlay, not a section.** Nothing here touches the timeline: no scene
moves, no narration shifts, the video is exactly as long as it was without it.
The card is composited over the first seconds of the assembled picture and that
is all it does — which is also why a project can turn it on or off between two
renders and get the same film either way.

Drawing follows the same rule as every other piece of text in this app (see
``render/text.py``): Pillow paints RGBA, FFmpeg composites. No ``drawtext``, no
libass, and no user-supplied string ever reaches a filtergraph — the titles exist
only as pixels in a PNG.

The typewriter is done with **one card per revealed state**, not one per frame:
a name is at most a couple of dozen characters, so a couple of dozen cards
covers the whole animation and each is shown with ``enable='between(t,a,b)'``.
They are then baked into a single transparent track, exactly as Shorts captions
are, so the assemble graph gains one input instead of twenty.

Every card is cached by a hash of everything that affects its pixels, so the
second render of the same project draws nothing at all.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.models.enums import IntroStyle
from app.models.project import LongIntro
from app.render import fonts

logger = logging.getLogger("evb.render.intro")

#: Bumped when anything here changes the pixels an intro produces. Folded into
#: the cache key so a cached track is never reused across renderer versions.
INTRO_RENDERER_VERSION = 2

#: Upper bound on typewriter cards. A longer name reveals more than one character
#: per step rather than producing a card per letter — past this the animation
#: reads identically and the cards are pure cost.
MAX_TYPING_STEPS = 28

#: The first implementation used three hard scale changes in 180 ms, which read
#: as a glitch rather than impact. These eased samples give the stamp a readable
#: landing and a tiny physical settle without requiring a per-frame PNG.
STAMP_LANDING_SECONDS = 0.42
_STAMP_SCALES = (1.48, 1.34, 1.22, 1.12, 1.04, 0.98, 1.015, 1.0)
SECONDARY_FADE_SECONDS = 0.45
_SECONDARY_OPACITIES = (0.18, 0.38, 0.62, 0.82, 1.0)
#: Degrees. A stamp that is perfectly level looks like a caption, not a stamp.
STAMP_ROTATION_DEGREES = -8.0

#: Layout, as fractions of frame height/width. Tuned at 1920x1080 and scaled from
#: there, so a 4K render produces the same composition rather than smaller type.
_TITLE_SIZE = 0.095
_SUBTITLE_SIZE = 0.032
_STAMP_SIZE = 0.060
_TITLE_CENTER_Y = 0.40
_SUBTITLE_GAP = 0.030
_STAMP_GAP = 0.045
_MAX_TITLE_WIDTH = 0.82


@dataclass(frozen=True)
class IntroCard:
    """One drawn state of the opening, and the window it is visible for."""

    path: Path
    start_seconds: float
    end_seconds: float


@dataclass
class IntroTrack:
    """Everything the pipeline needs to composite one branded opening."""

    cards: list[IntroCard] = field(default_factory=list)
    duration_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    #: Content address of the whole track, for the pre-composited file's name.
    digest: str = ""
    #: Set once the cards have been baked into a single transparent video.
    precomposed: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not self.cards


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _typing_steps(text: str) -> list[str]:
    """The revealed prefixes of ``text``, at most ``MAX_TYPING_STEPS`` of them."""
    if not text:
        return []
    length = len(text)
    if length <= MAX_TYPING_STEPS:
        return [text[: index + 1] for index in range(length)]
    stride = length / MAX_TYPING_STEPS
    prefixes: list[str] = []
    for step in range(MAX_TYPING_STEPS):
        cut = min(length, max(1, round((step + 1) * stride)))
        prefix = text[:cut]
        if prefix != (prefixes[-1] if prefixes else None):
            prefixes.append(prefix)
    if prefixes[-1] != text:
        prefixes.append(text)
    return prefixes


def _fitted_title_font(text: str, family: str, width: int, height: int):  # noqa: ANN201
    """The largest title size at which the full name still fits the frame."""
    size = max(16, int(round(height * _TITLE_SIZE)))
    available = width * _MAX_TITLE_WIDTH
    while size > 20:
        font = fonts.load(family, 900, size)
        tracking = size * 0.06
        measured = font.getlength(text) + max(0, len(text) - 1) * tracking
        if measured <= available:
            return font, size, tracking
        size -= 2
    font = fonts.load(family, 900, 20)
    return font, 20, 20 * 0.06


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,  # noqa: ANN001
    fill: tuple[int, int, int, int],
    tracking: float,
) -> float:
    """Draw ``text`` with manual letter-spacing; returns the width consumed."""
    x, y = xy
    start = x
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += font.getlength(character) + tracking
    return max(0.0, x - start - tracking)


def _tracked_width(text: str, font, tracking: float) -> float:  # noqa: ANN001
    if not text:
        return 0.0
    return font.getlength(text) + max(0, len(text) - 1) * tracking


def _stamp_layer(
    text: str,
    *,
    family: str,
    height: int,
    colour: tuple[int, int, int],
    scale: float,
    opacity: float,
) -> Image.Image:
    """The rotated stamp, drawn at ``scale`` and returned with its own alpha."""
    size = max(14, int(round(height * _STAMP_SIZE * scale)))
    font = fonts.load(family, 900, size)
    tracking = size * 0.14
    text_width = int(round(_tracked_width(text, font, tracking)))
    ascent, descent = font.getmetrics()
    text_height = ascent + descent

    pad_x = int(round(size * 0.42))
    pad_y = int(round(size * 0.26))
    border = max(2, int(round(size * 0.075)))
    box_width = text_width + 2 * pad_x
    box_height = text_height + 2 * pad_y
    bleed = border * 3

    layer = Image.new("RGBA", (box_width + 2 * bleed, box_height + 2 * bleed), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(round(255 * max(0.0, min(1.0, opacity))))
    draw.rounded_rectangle(
        [bleed, bleed, bleed + box_width, bleed + box_height],
        radius=int(round(size * 0.16)),
        outline=(*colour, alpha),
        width=border,
    )
    _draw_tracked(
        draw,
        (bleed + pad_x, bleed + pad_y),
        text,
        font,
        (*colour, alpha),
        tracking,
    )
    return layer.rotate(STAMP_ROTATION_DEGREES, resample=Image.BICUBIC, expand=True)


def _card_digest(
    intro: LongIntro,
    *,
    family: str,
    width: int,
    height: int,
    typed: str,
    show_cursor: bool,
    secondary_opacity: float,
    stamp_scale: float | None,
    stamp_opacity: float,
) -> str:
    payload = "\x1f".join(
        [
            str(INTRO_RENDERER_VERSION),
            intro.model_dump_json(),
            family,
            f"{width}x{height}",
            typed,
            "cursor" if show_cursor else "-",
            f"sub:{secondary_opacity:.3f}",
            "-" if stamp_scale is None else f"{stamp_scale:.3f}",
            f"stamp-alpha:{stamp_opacity:.3f}",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _render_card(
    intro: LongIntro,
    *,
    family: str,
    width: int,
    height: int,
    typed: str,
    show_cursor: bool,
    secondary_opacity: float,
    stamp_scale: float | None,
    stamp_opacity: float,
    output_dir: Path,
) -> Path:
    """Draw one state of the opening to a cached transparent PNG."""
    digest = _card_digest(
        intro,
        family=family,
        width=width,
        height=height,
        typed=typed,
        show_cursor=show_cursor,
        secondary_opacity=secondary_opacity,
        stamp_scale=stamp_scale,
        stamp_opacity=stamp_opacity,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"intro-{digest}.png"
    if target.is_file():
        return target

    title = intro.primary_title.upper()
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    if intro.scrim_opacity > 0:
        scrim = Image.new(
            "RGBA", (width, height), (0, 0, 0, int(round(255 * intro.scrim_opacity)))
        )
        image = Image.alpha_composite(image, scrim)

    font, size, tracking = _fitted_title_font(title or " ", family, width, height)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    title_top = int(round(height * _TITLE_CENTER_Y - line_height / 2))
    full_width = _tracked_width(title, font, tracking)
    typed_width = _tracked_width(typed, font, tracking)
    # The name is centred on its *finished* width, so the letters stay put as
    # they appear instead of sliding left with every keystroke.
    title_left = (width - full_width) / 2

    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    primary = (*_hex_to_rgb(intro.primary_color), 255)
    if typed:
        _draw_tracked(draw, (title_left, title_top), typed, font, primary, tracking)

    if show_cursor:
        cursor_width = max(2, int(round(size * 0.055)))
        cursor_x = title_left + typed_width + (tracking if typed else 0)
        draw.rectangle(
            [
                cursor_x,
                title_top + int(round(line_height * 0.18)),
                cursor_x + cursor_width,
                title_top + int(round(line_height * 0.86)),
            ],
            fill=primary,
        )

    bottom = title_top + line_height
    secondary = intro.secondary_title.strip()
    if secondary_opacity > 0 and secondary:
        sub_size = max(12, int(round(height * _SUBTITLE_SIZE)))
        sub_font = fonts.load(family, 500, sub_size)
        sub_tracking = sub_size * 0.22
        sub_width = _tracked_width(secondary, sub_font, sub_tracking)
        sub_top = bottom + int(round(height * _SUBTITLE_GAP))
        _draw_tracked(
            draw,
            ((width - sub_width) / 2, sub_top),
            secondary,
            sub_font,
            (
                *_hex_to_rgb(intro.secondary_color),
                int(round(235 * max(0.0, min(1.0, secondary_opacity)))),
            ),
            sub_tracking,
        )
        sub_ascent, sub_descent = sub_font.getmetrics()
        bottom = sub_top + sub_ascent + sub_descent

    # Blur the alpha into black. Blurring the coloured layer itself (the v1
    # behaviour) produced a pale halo and made each state appear to flash.
    shadow_alpha = text_layer.getchannel("A").filter(
        ImageFilter.GaussianBlur(int(round(height * 0.006)))
    )
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha.point(lambda alpha: int(alpha * 0.72)))
    image = Image.alpha_composite(image, shadow)
    image = Image.alpha_composite(image, text_layer)

    if stamp_scale is not None and intro.stamp_text.strip():
        text = intro.stamp_text.upper().strip()
        colour = _hex_to_rgb(intro.stamp_color)
        stamp = _stamp_layer(
            text, family=family, height=height, colour=colour,
            scale=stamp_scale, opacity=stamp_opacity,
        )
        # The stamp scales about the centre of where it will come to rest, so it
        # reads as landing on the frame rather than growing out of the subtitle.
        resting = (
            stamp
            if stamp_scale == 1.0
            else _stamp_layer(
                text, family=family, height=height, colour=colour, scale=1.0, opacity=1.0
            )
        )
        centre_y = bottom + height * _STAMP_GAP + resting.height / 2
        stamp_top = int(round(centre_y - stamp.height / 2))
        stamp_left = int(round((width - stamp.width) / 2))
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.alpha_composite(stamp, (max(0, stamp_left), max(0, stamp_top)))
        image = Image.alpha_composite(image, layer)

    image.save(target, "PNG")
    return target


def build_intro_track(
    intro: LongIntro,
    *,
    font_family: str,
    width: int,
    height: int,
    output_dir: Path,
) -> IntroTrack:
    """Draw every state of the opening, with the window each is visible for.

    ``intro`` must already be resolved (see ``Project.resolved_long_intro``), so
    the titles here are the ones that will actually be drawn.
    """
    title = intro.primary_title.upper().strip()
    if not intro.enabled or not (title or intro.stamp_text.strip()):
        return IntroTrack()

    stamped = intro.intro_style is IntroStyle.TYPEWRITER_STAMP and bool(intro.stamp_text.strip())
    typing = intro.intro_style is IntroStyle.TYPEWRITER_STAMP and intro.typewriter_duration > 0

    def card(
        start: float,
        end: float,
        *,
        typed: str,
        cursor: bool = False,
        secondary_opacity: float = 1.0,
        stamp_scale: float | None = None,
        stamp_opacity: float = 1.0,
    ) -> IntroCard | None:
        if end - start <= 1e-4:
            return None
        return IntroCard(
            path=_render_card(
                intro,
                family=font_family,
                width=width,
                height=height,
                typed=typed,
                show_cursor=cursor,
                secondary_opacity=secondary_opacity,
                stamp_scale=stamp_scale,
                stamp_opacity=stamp_opacity,
                output_dir=output_dir,
            ),
            start_seconds=round(start, 4),
            end_seconds=round(end, 4),
        )

    cards: list[IntroCard] = []
    typed_until = 0.0

    if typing and title:
        steps = _typing_steps(title)
        span = min(intro.typewriter_duration, intro.duration)
        for index, prefix in enumerate(steps):
            start = span * index / len(steps)
            end = span * (index + 1) / len(steps)
            entry = card(start, end, typed=prefix, cursor=True, secondary_opacity=0.0)
            if entry is not None:
                cards.append(entry)
        typed_until = span

    # Reveal the scientific name over several states. The old renderer switched
    # it on in one frame even though the UI and docs promised a fade.
    hold_end = min(intro.stamp_at, intro.duration) if stamped else intro.duration
    secondary = bool(intro.secondary_title.strip())
    fade_span = min(
        SECONDARY_FADE_SECONDS,
        max(0.0, hold_end - typed_until),
    )
    if secondary and fade_span > 0 and intro.intro_style is IntroStyle.TYPEWRITER_STAMP:
        for index, opacity in enumerate(_SECONDARY_OPACITIES):
            start = typed_until + fade_span * index / len(_SECONDARY_OPACITIES)
            end = typed_until + fade_span * (index + 1) / len(_SECONDARY_OPACITIES)
            entry = card(
                start, end, typed=title, secondary_opacity=opacity
            )
            if entry is not None:
                cards.append(entry)
        typed_until += fade_span
    entry = card(
        typed_until,
        max(typed_until, hold_end),
        typed=title,
        secondary_opacity=1.0,
    )
    if entry is not None:
        cards.append(entry)

    if stamped:
        landing = min(STAMP_LANDING_SECONDS, max(0.0, intro.duration - intro.stamp_at))
        for index, scale in enumerate(_STAMP_SCALES):
            start = intro.stamp_at + landing * index / len(_STAMP_SCALES)
            end = intro.stamp_at + landing * (index + 1) / len(_STAMP_SCALES)
            entry = card(
                start,
                end,
                typed=title,
                secondary_opacity=1.0,
                stamp_scale=scale,
                stamp_opacity=0.35 + 0.65 * (index + 1) / len(_STAMP_SCALES),
            )
            if entry is not None:
                cards.append(entry)
        entry = card(
            intro.stamp_at + landing,
            intro.duration,
            typed=title,
            secondary_opacity=1.0,
            stamp_scale=1.0,
        )
        if entry is not None:
            cards.append(entry)

    track = IntroTrack(
        cards=cards,
        duration_seconds=intro.duration,
        fade_out_seconds=min(intro.fade_out_seconds, intro.duration),
    )
    track.digest = track_digest(track, width=width, height=height)
    logger.info(
        "built a %d-card %s intro for %r (%.2fs)",
        len(cards), intro.intro_style.value, intro.primary_title, intro.duration,
    )
    return track


def track_digest(track: IntroTrack, *, width: int, height: int) -> str:
    """Content address for one pre-composited intro track."""
    payload = "\x1f".join(
        [
            str(INTRO_RENDERER_VERSION),
            f"{width}x{height}",
            f"{track.duration_seconds:.4f}",
            f"{track.fade_out_seconds:.4f}",
            *(
                f"{card.start_seconds:.4f}-{card.end_seconds:.4f}:{card.path.name}"
                for card in track.cards
            ),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def overlay_steps(
    track: IntroTrack,
    *,
    current: str,
    add_input,  # noqa: ANN001 - callable returning the new input's index
) -> tuple[list[str], str]:
    """Filter steps chaining every intro card onto ``current``.

    Only numbers this module computed are interpolated. The titles themselves
    are pixels in a PNG and never appear in the graph.
    """
    steps: list[str] = []
    fade = track.fade_out_seconds
    for number, entry in enumerate(track.cards):
        index = add_input(entry.path)
        label = f"intro{number}"
        # The fade-out is applied to whichever cards are on screen while it runs,
        # so the whole opening dissolves together instead of the last card
        # cutting out.
        overlap_start = max(entry.start_seconds, track.duration_seconds - fade)
        if fade > 0 and entry.end_seconds > overlap_start:
            steps.append(
                f"[{index}:v]format=rgba,"
                f"fade=t=out:st={overlap_start:.4f}:d={fade:.4f}:alpha=1[{label}]"
            )
        else:
            steps.append(f"[{index}:v]format=rgba[{label}]")
        steps.append(
            f"[{current}][{label}]overlay=0:0:"
            f"enable='between(t,{entry.start_seconds:.4f},{entry.end_seconds:.4f})'[on{label}]"
        )
        current = f"on{label}"
    return steps, current
