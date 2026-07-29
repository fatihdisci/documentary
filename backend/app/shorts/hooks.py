"""Drawing a Short's opening hook.

A hook is the first second and a half of a Short: two short lines, upper case,
sitting on the black band *above* the letterboxed picture. It is what decides
whether the Short is watched at all, so it is authored with the Short — see
``models/project.ShortHook`` — and drawn here.

This module is deliberately thin. Everything it needs already exists for
Shorts-native captions: the same fitter picks a type size that fits the lines,
the same ``render_card`` paints the RGBA PNG, and the same overlay convention
composites it. The only differences are the placement (top instead of bottom)
and the fact that a hook is a single card with a fixed window rather than a
track that follows the narration.

Two properties hold, as everywhere else in the render path: no user text ever
reaches a filtergraph — the words exist only as pixels in a PNG — and every
number interpolated into the graph is computed here from a validated model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.models.enums import TextPosition
from app.models.project import ShortHook
from app.render import fonts
from app.render.text import TextCard, render_card
from app.shorts.captions import as_text_style
from app.shorts.models import ShortHookStyle

logger = logging.getLogger("evb.shorts.hooks")

#: Bumped when anything here changes the pixels a hook produces. Folded into the
#: Short's cache key, so a finished Short is never served from a different
#: renderer than the one that drew it.
HOOK_RENDERER_VERSION = 1


@dataclass(frozen=True)
class HookCard:
    """One drawn hook and the window it is visible for."""

    card: TextCard
    start_seconds: float
    end_seconds: float
    fitted_font_size: int

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


def fit_hook_size(lines: list[str], style: ShortHookStyle, *, canvas_width: int) -> int:
    """The largest type size at which every authored line fits on its own line.

    A hook's line break is a writing decision — "WHEN HE DIED," then "THE SPECIES
    ENDED" — so unlike a caption, which is re-wrapped freely, a hook shrinks
    rather than re-breaks. Below ``min_font_scale`` it stops shrinking and lets
    ``render_card`` wrap the offending line, because unreadable type is worse
    than a line break in the wrong place.
    """
    available = canvas_width * style.max_width_ratio - 2 * style.box_padding_x
    floor = max(12, int(round(style.font_size * style.min_font_scale)))

    size = style.font_size
    while size >= floor:
        font = fonts.load(style.font_family, style.font_weight, size)
        widest = max(
            (font.getlength(line) + max(0, len(line) - 1) * style.letter_spacing)
            for line in lines
        )
        if widest <= available:
            return size
        size -= 2

    logger.info("hook type fitted down to the %dpx floor for %r", floor, " / ".join(lines))
    return floor


def build_hook_card(
    hook: ShortHook,
    style: ShortHookStyle,
    *,
    canvas_width: int,
    canvas_height: int,
    total_duration_seconds: float,
    output_dir: Path,
) -> HookCard | None:
    """Draw the hook, clipped to the Short it belongs to.

    Returns ``None`` when there is nothing to draw — no lines, the hook turned
    off, or a window that falls outside the Short — so the caller skips the
    overlay entirely rather than compositing an invisible image.
    """
    if not hook.is_visible:
        return None

    start = max(0.0, min(hook.start_seconds, total_duration_seconds))
    end = min(start + hook.duration_seconds, total_duration_seconds)
    if end - start <= 1e-3:
        logger.info("hook window falls outside a %.2fs Short; nothing drawn", total_duration_seconds)
        return None

    # Upper case is applied here rather than asked of the author: it is the
    # channel's look, and it must not depend on how the words were typed.
    lines = [line.upper() for line in hook.lines[: style.max_lines]]
    fitted = fit_hook_size(lines, style, canvas_width=canvas_width)

    card = render_card(
        "\n".join(lines),
        as_text_style(style, size=fitted),
        frame_width=canvas_width,
        frame_height=canvas_height,
        position=TextPosition.TOP_CENTER,
        margin=style.safe_top_inset,
        output_dir=output_dir,
    )
    if card is None:
        return None

    logger.info(
        "built a %d-line hook card at %dpx for a %dx%d canvas",
        len(lines), fitted, canvas_width, canvas_height,
    )
    return HookCard(
        card=card, start_seconds=start, end_seconds=end, fitted_font_size=fitted
    )


def overlay_steps(
    hook: HookCard,
    *,
    style: ShortHookStyle,
    current: str,
    add_input,  # noqa: ANN001 - callable returning the new input's index
) -> tuple[list[str], str]:
    """Filter steps compositing the hook card onto ``current``."""
    index = add_input(hook.card.path)
    fade = min(style.fade_seconds, max(0.0, hook.duration_seconds / 3))
    if fade > 0:
        steps = [
            f"[{index}:v]format=rgba,"
            f"fade=t=in:st={hook.start_seconds:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(hook.start_seconds, hook.end_seconds - fade):.3f}:"
            f"d={fade:.3f}:alpha=1[hookcard]"
        ]
    else:
        steps = [f"[{index}:v]format=rgba[hookcard]"]
    steps.append(
        f"[{current}][hookcard]overlay={hook.card.x}:{hook.card.y}:"
        f"enable='between(t,{hook.start_seconds:.3f},{hook.end_seconds:.3f})'[hooked]"
    )
    return steps, "hooked"
