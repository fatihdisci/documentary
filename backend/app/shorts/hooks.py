"""Draw a Short's opening as a two-beat cold open, not a static title card.

The first authored line sets up the thought. The second lands larger, in the
channel accent colour, with a short upward punch. Both sit around the vertical
canvas's eye line: central enough to stop the scroll, high enough to clear the
Shorts controls and native captions.

Text is still rendered to PNG by Pillow. User-authored words never reach the
FFmpeg filtergraph; only validated timings and renderer-computed coordinates do.
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

HOOK_RENDERER_VERSION = 4
IMPACT_DELAY_SECONDS = 0.42
IMPACT_SLIDE_SECONDS = 0.20
IMPACT_SLIDE_PIXELS = 46
EYE_LINE_TOP_RATIO = 0.39


@dataclass(frozen=True)
class HookCard:
    """The separately timed setup and impact cards of one opening hook."""

    #: Kept as ``card`` for callers: this is the dominant impact card.
    card: TextCard
    lead_card: TextCard | None
    start_seconds: float
    end_seconds: float
    impact_start_seconds: float
    fitted_font_size: int

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


def fit_hook_size(lines: list[str], style: ShortHookStyle, *, canvas_width: int) -> int:
    """Return the largest size that keeps every authored line intact."""
    available = canvas_width * style.max_width_ratio - 2 * style.box_padding_x
    floor = max(12, int(round(style.font_size * style.min_font_scale)))

    size = style.font_size
    while size >= floor:
        font = fonts.load(style.font_family, style.font_weight, size)
        widest = max(
            (
                font.getlength(line)
                + max(0, len(line) - 1) * style.letter_spacing
            )
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
    """Draw the hook's setup and impact, clipped to its Short."""
    if not hook.is_visible:
        return None

    start = max(0.0, min(hook.start_seconds, total_duration_seconds))
    end = min(start + hook.duration_seconds, total_duration_seconds)
    if end - start <= 1e-3:
        logger.info("hook window falls outside a %.2fs Short; nothing drawn", total_duration_seconds)
        return None

    lines = [line.upper() for line in hook.lines[: style.max_lines]]
    lead_text = lines[0] if len(lines) > 1 else ""
    impact_text = lines[-1]

    lead_style = style.model_copy(
        update={
            "font_size": 58,
            "font_weight": 700,
            "letter_spacing": 3.5,
            "color": "#D7D2C8",
            "shadow_blur": 18,
        }
    )
    impact_style = style.model_copy(
        update={
            "font_size": 92,
            "font_weight": 900,
            "letter_spacing": 1.0,
            "color": "#E3473D",
            "shadow_blur": 30,
        }
    )

    fitted = fit_hook_size([impact_text], impact_style, canvas_width=canvas_width)
    lead_margin = max(style.safe_top_inset, int(round(canvas_height * EYE_LINE_TOP_RATIO)))
    impact_margin = lead_margin + (112 if lead_text else 74)
    impact_card = render_card(
        impact_text,
        as_text_style(impact_style, size=fitted),
        frame_width=canvas_width,
        frame_height=canvas_height,
        position=TextPosition.TOP_CENTER,
        margin=impact_margin,
        output_dir=output_dir,
    )
    if impact_card is None:
        return None

    lead_card = None
    if lead_text:
        lead_size = fit_hook_size([lead_text], lead_style, canvas_width=canvas_width)
        lead_card = render_card(
            lead_text,
            as_text_style(lead_style, size=lead_size),
            frame_width=canvas_width,
            frame_height=canvas_height,
            position=TextPosition.TOP_CENTER,
            margin=lead_margin,
            output_dir=output_dir,
        )

    impact_start = min(end, start + IMPACT_DELAY_SECONDS) if lead_card else start
    logger.info(
        "built a %d-beat hook at %dpx for a %dx%d canvas",
        2 if lead_card else 1,
        fitted,
        canvas_width,
        canvas_height,
    )
    return HookCard(
        card=impact_card,
        lead_card=lead_card,
        start_seconds=start,
        end_seconds=end,
        impact_start_seconds=impact_start,
        fitted_font_size=fitted,
    )


def overlay_steps(
    hook: HookCard,
    *,
    style: ShortHookStyle,
    current: str,
    add_input,  # noqa: ANN001 - callable returning the new input's index
) -> tuple[list[str], str]:
    """Composite the setup, then punch the impact line upward into place."""
    steps: list[str] = []
    fade = min(style.fade_seconds, max(0.0, hook.duration_seconds / 3))

    if hook.lead_card is not None:
        lead_index = add_input(hook.lead_card.path)
        if fade > 0:
            steps.append(
                f"[{lead_index}:v]format=rgba,"
                f"fade=t=in:st={hook.start_seconds:.3f}:d={fade:.3f}:alpha=1,"
                f"fade=t=out:st={max(hook.start_seconds, hook.end_seconds - fade):.3f}:"
                f"d={fade:.3f}:alpha=1[hooklead]"
            )
        else:
            steps.append(f"[{lead_index}:v]format=rgba[hooklead]")
        steps.append(
            f"[{current}][hooklead]overlay={hook.lead_card.x}:{hook.lead_card.y}:"
            f"enable='between(t,{hook.start_seconds:.3f},{hook.end_seconds:.3f})'[withlead]"
        )
        current = "withlead"

    impact_index = add_input(hook.card.path)
    if fade > 0:
        steps.append(
            f"[{impact_index}:v]format=rgba,"
            f"fade=t=in:st={hook.impact_start_seconds:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(hook.start_seconds, hook.end_seconds - fade):.3f}:"
            f"d={fade:.3f}:alpha=1[hookcard]"
        )
    else:
        steps.append(f"[{impact_index}:v]format=rgba[hookcard]")

    slide_end = min(hook.end_seconds, hook.impact_start_seconds + IMPACT_SLIDE_SECONDS)
    slide_duration = max(0.001, slide_end - hook.impact_start_seconds)
    start_y = hook.card.y + IMPACT_SLIDE_PIXELS
    y_expression = (
        f"if(lt(t,{slide_end:.3f}),"
        f"{start_y}-{IMPACT_SLIDE_PIXELS}*(t-{hook.impact_start_seconds:.3f})/"
        f"{slide_duration:.3f},{hook.card.y})"
    )
    steps.append(
        f"[{current}][hookcard]overlay={hook.card.x}:'{y_expression}':"
        f"enable='between(t,{hook.impact_start_seconds:.3f},{hook.end_seconds:.3f})'[hooked]"
    )
    return steps, "hooked"
