"""Ken Burns pan-and-zoom.

Smoothness at 60 fps is the whole problem here. ``zoompan`` computes integer
crop offsets in the *input* image's coordinate space, so panning across a
1920x1080 source moves in whole-pixel jumps that read as stutter.

The fix is supersampling: the working image is rendered at ``N x`` the output
size (default 3x = 5760x3240), so one input pixel is a third of an output pixel
and the quantization falls below what the eye can see. Verified on this machine.

Motion is expressed as a function of the output frame index ``on`` with
smoothstep easing, so movement accelerates and decelerates rather than starting
and stopping abruptly.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.models.enums import AUTO_MOTION_ROTATION, AnimationPreset
from app.models.project import MAX_SCALE, Scene, Section

logger = logging.getLogger("evb.kenburns")

#: Default zoom travel for the "slow" presets. Deliberately small: documentary
#: motion should be barely perceptible, not a push-in.
DEFAULT_ZOOM_TRAVEL = 0.12

#: Baseline zoom for pans. This is a real trade-off: the crop at scale s is
#: 1/s of the image, so the centre can only move within [1/(2s), 1-1/(2s)] —
#: a total slack of (1 - 1/s). At 1.14 that slack is only 0.12 of the image,
#: which is barely a pan. 1.28 gives 0.22 of travel (~420px on a 1920 source)
#: while keeping the resample soft enough not to visibly degrade a 1080p image.
PAN_SCALE = 1.28

#: How much of the *available slack* a pan uses. Held below 1.0 so the frame
#: never reaches the exact edge, where a rounding error would expose a border.
PAN_TRAVEL = 0.85


def pan_extent(scale: float, fraction: float = PAN_TRAVEL) -> float:
    """Half the distance a pan may travel at ``scale``, in image fractions.

    Expressed against the slack rather than the whole image, so the geometry is
    valid by construction instead of being silently clamped afterwards.
    """
    slack = max(0.0, 1.0 - 1.0 / scale)
    return slack * fraction / 2.0


@dataclass(frozen=True)
class Motion:
    """A resolved camera move, in normalized coordinates.

    ``center_x``/``center_y`` are the centre of the visible crop as a fraction
    of the image (0.5 = centred). ``scale`` is the zoom factor: 1.0 shows the
    whole frame, 1.2 shows 1/1.2 of it.
    """

    preset: AnimationPreset
    start_scale: float
    end_scale: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float

    @property
    def is_static(self) -> bool:
        return (
            abs(self.start_scale - self.end_scale) < 1e-4
            and abs(self.start_x - self.end_x) < 1e-4
            and abs(self.start_y - self.end_y) < 1e-4
        )


def auto_preset_for(project_id: str, index: int, previous: AnimationPreset | None) -> AnimationPreset:
    """Pick a deterministic, non-repeating motion for a scene.

    Same project and index always yields the same result, and never the same
    effect twice in a row. Pans additionally never repeat direction back to back.
    """
    rotation = AUTO_MOTION_ROTATION
    # Seed the starting offset from the project id so different projects do not
    # all open with the same move, while a given project stays reproducible.
    seed = int(hashlib.sha256(project_id.encode()).hexdigest()[:8], 16)
    candidate = rotation[(seed + index) % len(rotation)]

    if previous is not None and candidate == previous:
        candidate = rotation[(seed + index + 1) % len(rotation)]

    # Avoid two pans along the same axis in sequence, which reads as a stutter
    # rather than a deliberate move.
    if previous is not None and _axis(candidate) is not None and _axis(candidate) == _axis(previous):
        for offset in range(1, len(rotation)):
            alternative = rotation[(seed + index + offset) % len(rotation)]
            if alternative != previous and _axis(alternative) != _axis(previous):
                return alternative
    return candidate


def _axis(preset: AnimationPreset) -> str | None:
    if preset in {AnimationPreset.PAN_LEFT_TO_RIGHT, AnimationPreset.PAN_RIGHT_TO_LEFT}:
        return "horizontal"
    if preset in {AnimationPreset.PAN_TOP_TO_BOTTOM, AnimationPreset.PAN_BOTTOM_TO_TOP}:
        return "vertical"
    return None


def resolve_motion(
    unit: Scene | Section,
    *,
    project_id: str,
    index: int,
    previous: AnimationPreset | None = None,
) -> Motion:
    """Turn a scene's animation settings into concrete start/end geometry."""
    preset = unit.animation_preset
    if preset is AnimationPreset.AUTO:
        preset = auto_preset_for(project_id, index, previous)

    focus_x = _clamp(unit.focus_x, 0.0, 1.0)
    focus_y = _clamp(unit.focus_y, 0.0, 1.0)

    motion = _preset_geometry(preset, focus_x, focus_y)

    # An explicit preset means the scene's own scale/offset fields were set by
    # hand in the editor, so honour them instead of the preset defaults.
    if unit.animation_preset not in {AnimationPreset.AUTO} and _has_manual_geometry(unit):
        motion = Motion(
            preset=preset,
            start_scale=unit.start_scale,
            end_scale=unit.end_scale,
            start_x=unit.start_x,
            start_y=unit.start_y,
            end_x=unit.end_x,
            end_y=unit.end_y,
        )

    return _clamp_motion(motion)


def _has_manual_geometry(unit: Scene | Section) -> bool:
    """True when the scene's geometry differs from the schema defaults."""
    return not (
        abs(unit.start_scale - 1.0) < 1e-6
        and abs(unit.start_x - 0.5) < 1e-6
        and abs(unit.start_y - 0.5) < 1e-6
        and abs(unit.end_x - 0.5) < 1e-6
        and abs(unit.end_y - 0.5) < 1e-6
    )


def _preset_geometry(preset: AnimationPreset, focus_x: float, focus_y: float) -> Motion:
    travel = DEFAULT_ZOOM_TRAVEL
    half = pan_extent(PAN_SCALE)

    match preset:
        case AnimationPreset.STATIC:
            return Motion(preset, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5)

        case AnimationPreset.SLOW_ZOOM_IN:
            return Motion(preset, 1.0, 1.0 + travel, 0.5, 0.5, 0.5, 0.5)

        case AnimationPreset.SLOW_ZOOM_OUT:
            return Motion(preset, 1.0 + travel, 1.0, 0.5, 0.5, 0.5, 0.5)

        case AnimationPreset.PAN_LEFT_TO_RIGHT:
            return Motion(preset, PAN_SCALE, PAN_SCALE, 0.5 - half, 0.5, 0.5 + half, 0.5)

        case AnimationPreset.PAN_RIGHT_TO_LEFT:
            return Motion(preset, PAN_SCALE, PAN_SCALE, 0.5 + half, 0.5, 0.5 - half, 0.5)

        case AnimationPreset.PAN_TOP_TO_BOTTOM:
            return Motion(preset, PAN_SCALE, PAN_SCALE, 0.5, 0.5 - half, 0.5, 0.5 + half)

        case AnimationPreset.PAN_BOTTOM_TO_TOP:
            return Motion(preset, PAN_SCALE, PAN_SCALE, 0.5, 0.5 + half, 0.5, 0.5 - half)

        case AnimationPreset.ZOOM_TO_CENTER:
            return Motion(preset, 1.0, 1.0 + travel * 1.5, 0.5, 0.5, 0.5, 0.5)

        case AnimationPreset.ZOOM_TO_LEFT:
            return Motion(preset, 1.0, 1.0 + travel * 1.5, 0.5, 0.5, 0.32, 0.5)

        case AnimationPreset.ZOOM_TO_RIGHT:
            return Motion(preset, 1.0, 1.0 + travel * 1.5, 0.5, 0.5, 0.68, 0.5)

        case AnimationPreset.ZOOM_TO_FOCUS:
            return Motion(preset, 1.0, 1.0 + travel * 1.6, 0.5, 0.5, focus_x, focus_y)

        case AnimationPreset.GENTLE_DIAGONAL:
            # A diagonal moves on both axes, so it uses less travel per axis to
            # keep the total movement comparable to a straight pan.
            diagonal = pan_extent(PAN_SCALE, PAN_TRAVEL * 0.7)
            return Motion(
                preset,
                PAN_SCALE,
                PAN_SCALE + 0.04,
                0.5 - diagonal,
                0.5 - diagonal,
                0.5 + diagonal,
                0.5 + diagonal,
            )

        case _:
            return Motion(AnimationPreset.SLOW_ZOOM_IN, 1.0, 1.0 + travel, 0.5, 0.5, 0.5, 0.5)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_motion(motion: Motion) -> Motion:
    """Keep the visible crop inside the image at both ends of the move.

    At scale ``s`` the crop is ``1/s`` of the image, so its centre must stay
    within ``[1/(2s), 1 - 1/(2s)]``. Violating this is what produces black
    borders, so it is corrected here rather than trusted to the caller.
    """
    start_scale = _clamp(motion.start_scale, 1.0, MAX_SCALE)
    end_scale = _clamp(motion.end_scale, 1.0, MAX_SCALE)

    def bound(scale: float, value: float) -> float:
        limit = 1.0 / (2.0 * scale)
        return _clamp(value, limit, 1.0 - limit)

    return Motion(
        preset=motion.preset,
        start_scale=start_scale,
        end_scale=end_scale,
        start_x=bound(start_scale, motion.start_x),
        start_y=bound(start_scale, motion.start_y),
        end_x=bound(end_scale, motion.end_x),
        end_y=bound(end_scale, motion.end_y),
    )


def smoothstep_expression(progress: str = "P") -> str:
    """Smoothstep easing ``3p^2 - 2p^3`` as an FFmpeg expression fragment."""
    return f"({progress}*{progress}*(3-2*{progress}))"


def build_zoompan_filter(
    motion: Motion,
    *,
    frames: int,
    output_width: int,
    output_height: int,
    fps: int,
    supersample: float,
) -> str:
    """Build the ``scale,zoompan`` filter chain for one scene.

    The source is first scaled to ``supersample x`` the output size with
    lanczos, then zoompan crops and resamples down to the output size. That
    upscale is what makes the motion smooth; without it, x/y quantization in the
    source's coordinate space produces visible stepping at 60 fps.
    """
    work_width = _even(int(output_width * supersample))
    work_height = _even(int(output_height * supersample))

    # zoompan advances `on` from 0 to frames-1. Guard against a 1-frame scene.
    denominator = max(1, frames - 1)
    progress = f"(on/{denominator})"
    eased = smoothstep_expression(progress)

    zoom_expr = f"{motion.start_scale:.6f}+({motion.end_scale - motion.start_scale:.6f})*{eased}"

    # x/y are the top-left of the crop in the *scaled* image. The crop is
    # iw/zoom wide, so centring on cx means x = cx*iw - (iw/zoom)/2.
    x_center = f"({motion.start_x:.6f}+({motion.end_x - motion.start_x:.6f})*{eased})"
    y_center = f"({motion.start_y:.6f}+({motion.end_y - motion.start_y:.6f})*{eased})"
    x_expr = f"iw*{x_center}-(iw/zoom)/2"
    y_expr = f"ih*{y_center}-(ih/zoom)/2"

    return (
        f"scale={work_width}:{work_height}:flags=lanczos,"
        f"setsar=1,"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d={frames}:"
        f"s={output_width}x{output_height}:"
        f"fps={fps}"
    )


def _even(value: int) -> int:
    """Round up to an even number; H.264 with yuv420p requires even dimensions."""
    return value + (value % 2)


def sample_transform(motion: Motion, progress: float) -> tuple[float, float, float]:
    """Evaluate the motion at ``progress`` in [0,1] -> (scale, center_x, center_y).

    This is the reference implementation the frontend canvas preview mirrors;
    a parity test asserts the two agree.
    """
    p = _clamp(progress, 0.0, 1.0)
    eased = p * p * (3 - 2 * p)
    return (
        motion.start_scale + (motion.end_scale - motion.start_scale) * eased,
        motion.start_x + (motion.end_x - motion.start_x) * eased,
        motion.start_y + (motion.end_y - motion.start_y) * eased,
    )


def describe(motion: Motion) -> str:
    """Human-readable summary, shown in the scene editor."""
    if motion.is_static:
        return "Static — no movement"
    parts: list[str] = []
    if motion.end_scale > motion.start_scale + 1e-4:
        parts.append(f"zoom in {motion.start_scale:.2f}x to {motion.end_scale:.2f}x")
    elif motion.start_scale > motion.end_scale + 1e-4:
        parts.append(f"zoom out {motion.start_scale:.2f}x to {motion.end_scale:.2f}x")
    dx = motion.end_x - motion.start_x
    dy = motion.end_y - motion.start_y
    if abs(dx) > 1e-4:
        parts.append(f"pan {'right' if dx > 0 else 'left'}")
    if abs(dy) > 1e-4:
        parts.append(f"pan {'down' if dy > 0 else 'up'}")
    return ", ".join(parts) or "Static"
