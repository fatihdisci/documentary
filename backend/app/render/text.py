"""Text rendering with Pillow.

This module exists because the FFmpeg build this app targets has no
``drawtext`` filter (no libfreetype) and no ``subtitles`` filter (no libass).
Every piece of on-screen text — titles, subtitles, captions, watermarks, burned
captions — is drawn here into an RGBA PNG, which FFmpeg then composites with
``overlay``.

The result is better than ``drawtext`` would have been anyway: bundled fonts
mean identical output on every machine, and we get rounded background boxes,
letter-spacing and real multi-line control.

Cards are cached by a hash of everything that affects their pixels, so
re-rendering a scene after a motion tweak costs nothing here.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.models.enums import TextPosition
from app.models.project import TextStyle
from app.render import fonts

logger = logging.getLogger("evb.text")

#: Padding added around the drawn card so shadow and blur are not clipped.
_BLEED = 48


@dataclass(frozen=True)
class TextCard:
    """A rendered overlay image and where it sits in the frame."""

    path: Path
    width: int
    height: int
    #: Top-left position of the *PNG* in the output frame. The image carries
    #: ``_BLEED`` pixels of transparent margin on every side so shadow and blur
    #: are not clipped, so this is ``_BLEED`` above and left of the visible box.
    x: int
    y: int
    text: str

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    # --- the visible box, for layout assertions and logs ------------------
    #
    # Everything a caller reasons about ("is the caption below the picture?")
    # is about the drawn box, not the transparent bleed around it.

    @property
    def box_x(self) -> int:
        return self.x + _BLEED

    @property
    def box_y(self) -> int:
        return self.y + _BLEED

    @property
    def box_width(self) -> int:
        return max(0, self.width - 2 * _BLEED)

    @property
    def box_height(self) -> int:
        return max(0, self.height - 2 * _BLEED)

    @property
    def box_bottom(self) -> int:
        return self.box_y + self.box_height


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def card_hash(
    text: str,
    style: TextStyle,
    *,
    frame_width: int,
    frame_height: int,
    position: TextPosition,
    margin: int,
) -> str:
    """Hash of every input that changes the rendered pixels."""
    payload = "\x1f".join(
        [
            text,
            style.model_dump_json(),
            str(frame_width),
            str(frame_height),
            position.value,
            str(margin),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def wrap_text(text: str, font, max_width: int, *, letter_spacing: float = 0.0) -> list[str]:
    """Wrap ``text`` to ``max_width`` pixels, honouring explicit newlines."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        buffer = ""
        for word in words:
            candidate = f"{buffer} {word}".strip()
            if buffer and _text_width(candidate, font, letter_spacing) > max_width:
                lines.append(buffer)
                buffer = word
            else:
                buffer = candidate
        if buffer:
            lines.append(buffer)
    return lines


def _text_width(text: str, font, letter_spacing: float) -> float:
    base = font.getlength(text)
    return base + max(0, len(text) - 1) * letter_spacing


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,
    fill: tuple[int, int, int, int],
    letter_spacing: float,
) -> None:
    """Draw text, applying letter-spacing manually (Pillow has no tracking)."""
    if letter_spacing == 0:
        draw.text(xy, text, font=font, fill=fill)
        return
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += font.getlength(character) + letter_spacing


def render_card(
    text: str,
    style: TextStyle,
    *,
    frame_width: int,
    frame_height: int,
    position: TextPosition,
    margin: int,
    output_dir: Path,
    cache: bool = True,
) -> TextCard | None:
    """Render one text overlay to a transparent PNG.

    Returns None for empty text so callers can skip the overlay entirely rather
    than compositing an invisible image.
    """
    text = (text or "").strip()
    if not text:
        return None

    digest = card_hash(
        text,
        style,
        frame_width=frame_width,
        frame_height=frame_height,
        position=position,
        margin=margin,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"card-{digest}.png"

    font = fonts.load(style.font_family, style.font_weight, style.size)
    max_text_width = int(frame_width * style.max_width_ratio) - 2 * style.box_padding_x
    lines = wrap_text(text, font, max_text_width, letter_spacing=style.letter_spacing)

    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * style.line_spacing)
    text_width = int(max(_text_width(line, font, style.letter_spacing) for line in lines))
    text_height = line_height * len(lines)

    box_width = text_width + 2 * style.box_padding_x
    box_height = text_height + 2 * style.box_padding_y
    canvas_width = box_width + 2 * _BLEED
    canvas_height = box_height + 2 * _BLEED

    x, y = _place(position, frame_width, frame_height, box_width, box_height, margin)

    if cache and target.is_file():
        return TextCard(
            path=target,
            width=canvas_width,
            height=canvas_height,
            x=x - _BLEED,
            y=y - _BLEED,
            text=text,
        )

    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    if style.box and style.box_opacity > 0:
        box_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(box_layer).rounded_rectangle(
            [_BLEED, _BLEED, _BLEED + box_width, _BLEED + box_height],
            radius=style.box_radius,
            fill=(*_hex_to_rgb(style.box_color), int(255 * style.box_opacity)),
        )
        image = Image.alpha_composite(image, box_layer)

    if style.shadow and style.shadow_blur > 0:
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        _draw_lines(
            shadow_draw, lines, font, style,
            origin_x=_BLEED + style.box_padding_x + style.shadow_offset,
            origin_y=_BLEED + style.box_padding_y + style.shadow_offset,
            line_height=line_height,
            fill=(0, 0, 0, 190),
            box_width=box_width,
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(style.shadow_blur / 2))
        image = Image.alpha_composite(image, shadow)

    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    _draw_lines(
        text_draw, lines, font, style,
        origin_x=_BLEED + style.box_padding_x,
        origin_y=_BLEED + style.box_padding_y,
        line_height=line_height,
        fill=(*_hex_to_rgb(style.color), 255),
        box_width=box_width,
        outline=(
            (style.outline_width, (*_hex_to_rgb(style.outline_color), 255))
            if style.outline_width > 0
            else None
        ),
    )
    image = Image.alpha_composite(image, text_layer)
    image.save(target, "PNG")
    logger.debug("rendered text card %s (%dx%d) for %r", target.name, canvas_width, canvas_height, text[:40])

    return TextCard(
        path=target,
        width=canvas_width,
        height=canvas_height,
        x=x - _BLEED,
        y=y - _BLEED,
        text=text,
    )


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    style: TextStyle,
    *,
    origin_x: int,
    origin_y: int,
    line_height: int,
    fill: tuple[int, int, int, int],
    box_width: int,
    outline: tuple[int, tuple[int, int, int, int]] | None = None,
) -> None:
    # Lines are left-aligned within the box; the box itself is what gets
    # positioned in the frame by _place().
    for index, line in enumerate(lines):
        offset_x = origin_x
        offset_y = origin_y + index * line_height

        if outline is not None:
            radius, colour = outline
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if dx or dy:
                        _draw_tracked(
                            draw, (offset_x + dx, offset_y + dy), line, font, colour,
                            style.letter_spacing,
                        )
        _draw_tracked(draw, (offset_x, offset_y), line, font, fill, style.letter_spacing)


def _place(
    position: TextPosition,
    frame_width: int,
    frame_height: int,
    box_width: int,
    box_height: int,
    margin: int,
) -> tuple[int, int]:
    """Top-left position of the box for a nine-point placement."""
    vertical, _, horizontal = position.value.partition("-")

    if horizontal == "left":
        x = margin
    elif horizontal == "center":
        x = (frame_width - box_width) // 2
    else:
        x = frame_width - box_width - margin

    if vertical == "top":
        y = margin
    elif vertical == "middle":
        y = (frame_height - box_height) // 2
    else:
        y = frame_height - box_height - margin

    # Never let a long line push the card off-frame.
    x = max(0, min(x, max(0, frame_width - box_width)))
    y = max(0, min(y, max(0, frame_height - box_height)))
    return x, y


def render_scrim(
    *,
    frame_width: int,
    frame_height: int,
    opacity: float,
    output_dir: Path,
    height_ratio: float = 0.42,
) -> Path | None:
    """A soft dark gradient behind lower-third text, for readability.

    Cheaper and better looking than raising the box opacity: the picture stays
    visible while the text still reads on a bright image.
    """
    if opacity <= 0:
        return None

    digest = hashlib.sha256(
        f"scrim{frame_width}x{frame_height}:{opacity}:{height_ratio}".encode()
    ).hexdigest()[:16]
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"scrim-{digest}.png"
    if target.is_file():
        return target

    band_height = int(frame_height * height_ratio)
    image = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    pixels = image.load()
    assert pixels is not None

    peak = int(255 * opacity)
    for row in range(band_height):
        # Quadratic ramp: invisible at the top of the band, strongest at the base.
        progress = row / max(1, band_height - 1)
        alpha = int(peak * progress**2)
        y = frame_height - band_height + row
        for column in range(frame_width):
            pixels[column, y] = (0, 0, 0, alpha)

    image.save(target, "PNG")
    return target
