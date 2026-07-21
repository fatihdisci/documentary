"""Transition presets mapped onto FFmpeg's ``xfade``.

The restraint rule from the project brief lives here: only cross dissolve, fade
through black and hard cuts are ever chosen automatically. Slides, white flashes,
blur and zoom exist but must be selected explicitly per scene — an unattended
render never produces one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import RESTRAINED_TRANSITIONS, TransitionPreset


@dataclass(frozen=True)
class TransitionSpec:
    """How one preset maps onto xfade."""

    preset: TransitionPreset
    #: xfade transition name, or None for a hard cut.
    xfade: str | None
    label: str
    description: str
    #: Multiplier applied to the configured duration. Some looks need longer.
    duration_scale: float = 1.0
    #: True when the app may pick this on its own.
    restrained: bool = False


SPECS: dict[TransitionPreset, TransitionSpec] = {
    TransitionPreset.NONE: TransitionSpec(
        preset=TransitionPreset.NONE,
        xfade=None,
        label="No transition",
        description="A hard cut. Use deliberately, for a change of subject.",
        restrained=True,
    ),
    TransitionPreset.DOCUMENTARY_DISSOLVE: TransitionSpec(
        preset=TransitionPreset.DOCUMENTARY_DISSOLVE,
        xfade="fade",
        label="Documentary dissolve",
        description="The default. A gentle cross dissolve that stays out of the way.",
        restrained=True,
    ),
    TransitionPreset.CROSS_DISSOLVE: TransitionSpec(
        preset=TransitionPreset.CROSS_DISSOLVE,
        xfade="dissolve",
        label="Cross dissolve",
        description="A slightly grainier blend than the documentary dissolve.",
        restrained=True,
    ),
    TransitionPreset.FADE_THROUGH_BLACK: TransitionSpec(
        preset=TransitionPreset.FADE_THROUGH_BLACK,
        xfade="fadeblack",
        label="Fade through black",
        description="Passes through black. Good for a change of chapter.",
        restrained=True,
    ),
    TransitionPreset.SLOW_CINEMATIC_DISSOLVE: TransitionSpec(
        preset=TransitionPreset.SLOW_CINEMATIC_DISSOLVE,
        xfade="fadeslow",
        label="Slow cinematic dissolve",
        description="A longer, softer blend. Uses 1.6x the configured duration.",
        duration_scale=1.6,
    ),
    TransitionPreset.DIP_TO_BLACK: TransitionSpec(
        preset=TransitionPreset.DIP_TO_BLACK,
        xfade="fadeblack",
        label="Dip to black",
        description="A quicker, more decisive pass through black.",
        duration_scale=0.7,
    ),
    TransitionPreset.FADE_THROUGH_WHITE: TransitionSpec(
        preset=TransitionPreset.FADE_THROUGH_WHITE,
        xfade="fadewhite",
        label="Fade through white",
        description="A bright flash. Strong effect — use sparingly.",
    ),
    TransitionPreset.SUBTLE_ZOOM_DISSOLVE: TransitionSpec(
        preset=TransitionPreset.SUBTLE_ZOOM_DISSOLVE,
        xfade="zoomin",
        label="Subtle zoom dissolve",
        description="Blends while pushing in slightly.",
    ),
    TransitionPreset.HORIZONTAL_SLIDE: TransitionSpec(
        preset=TransitionPreset.HORIZONTAL_SLIDE,
        xfade="slideleft",
        label="Horizontal slide",
        description="The next scene slides in from the right.",
    ),
    TransitionPreset.VERTICAL_SLIDE: TransitionSpec(
        preset=TransitionPreset.VERTICAL_SLIDE,
        xfade="slideup",
        label="Vertical slide",
        description="The next scene slides up from below.",
    ),
    TransitionPreset.BLUR_DISSOLVE: TransitionSpec(
        preset=TransitionPreset.BLUR_DISSOLVE,
        xfade="hblur",
        label="Blur dissolve",
        description="Blurs through the change. Noticeable; use for a time jump.",
    ),
}


def spec_for(preset: TransitionPreset) -> TransitionSpec:
    return SPECS[preset]


def xfade_name(preset: TransitionPreset) -> str | None:
    """The xfade transition name, or None when this is a hard cut."""
    return SPECS[preset].xfade


def effective_duration(preset: TransitionPreset, configured: float) -> float:
    """Apply the preset's duration scaling."""
    spec = SPECS[preset]
    if spec.xfade is None:
        return 0.0
    return configured * spec.duration_scale


def is_restrained(preset: TransitionPreset) -> bool:
    """True when the app is allowed to choose this without being told to."""
    return preset in RESTRAINED_TRANSITIONS


def restrained_choices() -> list[TransitionSpec]:
    """The presets offered as project defaults."""
    return [spec for spec in SPECS.values() if spec.restrained]


def all_choices() -> list[TransitionSpec]:
    """Everything, for the per-scene override picker."""
    return list(SPECS.values())


def supported_by(available_filters: frozenset[str]) -> bool:
    """Whether transitions can render at all on this FFmpeg build."""
    return "xfade" in available_filters
