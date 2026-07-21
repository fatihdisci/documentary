"""Ken Burns motion: geometry, determinism and border safety."""

from __future__ import annotations

import pytest

from app.models.enums import AUTO_MOTION_ROTATION, AnimationPreset
from app.models.project import MAX_SCALE, Scene
from app.render.kenburns import (
    PAN_SCALE,
    Motion,
    auto_preset_for,
    build_zoompan_filter,
    describe,
    pan_extent,
    resolve_motion,
    sample_transform,
)


def motion_for(preset: AnimationPreset, **kwargs: object) -> Motion:
    scene = Scene(animation_preset=preset, **kwargs)  # type: ignore[arg-type]
    return resolve_motion(scene, project_id="test-project", index=0)


class TestBorderSafety:
    """The crop must never leave the image, at either end of the move."""

    @pytest.mark.parametrize("preset", [p for p in AnimationPreset if p is not AnimationPreset.AUTO])
    def test_crop_stays_inside_the_image(self, preset: AnimationPreset) -> None:
        motion = motion_for(preset)
        for scale, x, y in (
            (motion.start_scale, motion.start_x, motion.start_y),
            (motion.end_scale, motion.end_x, motion.end_y),
        ):
            half = 1.0 / (2.0 * scale)
            assert x - half >= -1e-6, f"{preset.value}: left edge exposed"
            assert x + half <= 1.0 + 1e-6, f"{preset.value}: right edge exposed"
            assert y - half >= -1e-6, f"{preset.value}: top edge exposed"
            assert y + half <= 1.0 + 1e-6, f"{preset.value}: bottom edge exposed"

    def test_holds_at_every_point_along_the_move(self) -> None:
        """Interpolation must not wander outside even if the endpoints are legal."""
        for preset in (p for p in AnimationPreset if p is not AnimationPreset.AUTO):
            motion = motion_for(preset)
            for step in range(21):
                scale, x, y = sample_transform(motion, step / 20)
                half = 1.0 / (2.0 * scale)
                assert half - 1e-6 <= x <= 1.0 - half + 1e-6, f"{preset.value} at {step / 20}"
                assert half - 1e-6 <= y <= 1.0 - half + 1e-6, f"{preset.value} at {step / 20}"

    def test_scale_never_exceeds_the_limit(self) -> None:
        for preset in (p for p in AnimationPreset if p is not AnimationPreset.AUTO):
            motion = motion_for(preset)
            assert 1.0 <= motion.start_scale <= MAX_SCALE
            assert 1.0 <= motion.end_scale <= MAX_SCALE

    def test_an_out_of_range_request_is_pulled_back_in(self) -> None:
        """A hand-edited scene asking for an illegal crop is corrected, not honoured."""
        scene = Scene(animation_preset=AnimationPreset.ZOOM_TO_FOCUS, start_scale=1.05,
                      end_scale=1.05, start_x=0.02, end_x=0.98, start_y=0.5, end_y=0.5)
        motion = resolve_motion(scene, project_id="p", index=0)
        half = 1.0 / (2.0 * motion.start_scale)
        assert motion.start_x >= half - 1e-6
        assert motion.end_x <= 1.0 - half + 1e-6


class TestPanExtent:
    def test_travel_is_derived_from_available_slack(self) -> None:
        """Pans must not rely on clamping to make their geometry legal."""
        extent = pan_extent(PAN_SCALE)
        limit = 1.0 / (2.0 * PAN_SCALE)
        assert 0.5 - extent >= limit, "the pan start would be clamped"
        assert 0.5 + extent <= 1.0 - limit, "the pan end would be clamped"

    def test_a_bigger_zoom_allows_a_longer_pan(self) -> None:
        assert pan_extent(1.5) > pan_extent(1.2)

    def test_no_zoom_means_no_pan_room(self) -> None:
        assert pan_extent(1.0) == pytest.approx(0.0)

    def test_a_pan_actually_moves_a_visible_distance(self) -> None:
        motion = motion_for(AnimationPreset.PAN_LEFT_TO_RIGHT)
        travel = abs(motion.end_x - motion.start_x)
        # On a 1920-wide source this is ~350px; anything much less is not a pan.
        assert travel > 0.12, f"pan travel of {travel:.3f} is too small to read as movement"


class TestPresets:
    def test_zoom_in_and_out_are_opposites(self) -> None:
        zoom_in = motion_for(AnimationPreset.SLOW_ZOOM_IN)
        zoom_out = motion_for(AnimationPreset.SLOW_ZOOM_OUT)
        assert zoom_in.end_scale > zoom_in.start_scale
        assert zoom_out.end_scale < zoom_out.start_scale

    def test_pan_directions_are_opposites(self) -> None:
        ltr = motion_for(AnimationPreset.PAN_LEFT_TO_RIGHT)
        rtl = motion_for(AnimationPreset.PAN_RIGHT_TO_LEFT)
        assert ltr.end_x > ltr.start_x
        assert rtl.end_x < rtl.start_x

    def test_vertical_pans_move_only_vertically(self) -> None:
        motion = motion_for(AnimationPreset.PAN_TOP_TO_BOTTOM)
        assert motion.end_y > motion.start_y
        assert motion.start_x == pytest.approx(motion.end_x)

    def test_static_does_not_move(self) -> None:
        assert motion_for(AnimationPreset.STATIC).is_static

    def test_zoom_to_focus_moves_toward_the_focus_point(self) -> None:
        motion = motion_for(AnimationPreset.ZOOM_TO_FOCUS, focus_x=0.25, focus_y=0.75)
        assert motion.end_x < motion.start_x, "should move left toward focus"
        assert motion.end_y > motion.start_y, "should move down toward focus"
        assert motion.end_scale > motion.start_scale

    def test_diagonal_moves_on_both_axes(self) -> None:
        motion = motion_for(AnimationPreset.GENTLE_DIAGONAL)
        assert motion.end_x != motion.start_x
        assert motion.end_y != motion.start_y

    def test_describe_is_human_readable(self) -> None:
        assert "zoom in" in describe(motion_for(AnimationPreset.SLOW_ZOOM_IN))
        assert "pan right" in describe(motion_for(AnimationPreset.PAN_LEFT_TO_RIGHT))
        assert describe(motion_for(AnimationPreset.STATIC)).startswith("Static")


class TestAutoVariation:
    def test_is_deterministic(self) -> None:
        first = [auto_preset_for("project-a", i, None) for i in range(10)]
        second = [auto_preset_for("project-a", i, None) for i in range(10)]
        assert first == second

    def test_different_projects_differ(self) -> None:
        a = [auto_preset_for("project-a", i, None) for i in range(6)]
        b = [auto_preset_for("project-zzz", i, None) for i in range(6)]
        assert a != b

    def test_never_repeats_the_previous_effect(self) -> None:
        previous: AnimationPreset | None = None
        for index in range(30):
            chosen = auto_preset_for("project-a", index, previous)
            assert chosen != previous, f"repeated {chosen.value} at index {index}"
            previous = chosen

    def test_never_pans_the_same_axis_twice_in_a_row(self) -> None:
        horizontal = {AnimationPreset.PAN_LEFT_TO_RIGHT, AnimationPreset.PAN_RIGHT_TO_LEFT}
        vertical = {AnimationPreset.PAN_TOP_TO_BOTTOM, AnimationPreset.PAN_BOTTOM_TO_TOP}
        previous: AnimationPreset | None = None
        for index in range(30):
            chosen = auto_preset_for("project-a", index, previous)
            if previous is not None:
                assert not (chosen in horizontal and previous in horizontal)
                assert not (chosen in vertical and previous in vertical)
            previous = chosen

    def test_only_uses_the_restrained_rotation(self) -> None:
        chosen = {auto_preset_for("p", i, None) for i in range(40)}
        assert chosen <= set(AUTO_MOTION_ROTATION)

    def test_a_whole_project_gets_varied_motion(self) -> None:
        """A 10-scene project should not look like the same move ten times."""
        previous: AnimationPreset | None = None
        chosen: list[AnimationPreset] = []
        for index in range(10):
            preset = auto_preset_for("the-dodo", index, previous)
            chosen.append(preset)
            previous = preset
        assert len(set(chosen)) >= 4, f"too repetitive: {[c.value for c in chosen]}"


class TestEasing:
    def test_starts_and_ends_at_the_endpoints(self) -> None:
        motion = motion_for(AnimationPreset.SLOW_ZOOM_IN)
        start_scale, _, _ = sample_transform(motion, 0.0)
        end_scale, _, _ = sample_transform(motion, 1.0)
        assert start_scale == pytest.approx(motion.start_scale)
        assert end_scale == pytest.approx(motion.end_scale)

    def test_is_monotonic(self) -> None:
        motion = motion_for(AnimationPreset.SLOW_ZOOM_IN)
        scales = [sample_transform(motion, i / 20)[0] for i in range(21)]
        assert scales == sorted(scales)

    def test_accelerates_then_decelerates(self) -> None:
        """Smoothstep: slow at the ends, fastest in the middle."""
        motion = motion_for(AnimationPreset.PAN_LEFT_TO_RIGHT)
        positions = [sample_transform(motion, i / 20)[1] for i in range(21)]
        deltas = [positions[i] - positions[i - 1] for i in range(1, len(positions))]
        assert deltas[len(deltas) // 2] > deltas[0] * 1.5
        assert deltas[len(deltas) // 2] > deltas[-1] * 1.5

    def test_progress_is_clamped(self) -> None:
        motion = motion_for(AnimationPreset.SLOW_ZOOM_IN)
        assert sample_transform(motion, -5.0)[0] == pytest.approx(motion.start_scale)
        assert sample_transform(motion, 99.0)[0] == pytest.approx(motion.end_scale)


class TestFilterGeneration:
    def test_uses_supersampled_working_size(self) -> None:
        filter_string = build_zoompan_filter(
            motion_for(AnimationPreset.SLOW_ZOOM_IN),
            frames=120, output_width=1920, output_height=1080, fps=60, supersample=3.0,
        )
        assert "scale=5760:3240:flags=lanczos" in filter_string
        assert "s=1920x1080" in filter_string
        assert "fps=60" in filter_string
        assert "d=120" in filter_string

    def test_working_size_is_always_even(self) -> None:
        filter_string = build_zoompan_filter(
            motion_for(AnimationPreset.STATIC),
            frames=60, output_width=1920, output_height=1080, fps=60, supersample=2.5,
        )
        scale_part = filter_string.split("scale=")[1].split(":flags")[0]
        width, height = (int(v) for v in scale_part.split(":"))
        assert width % 2 == 0 and height % 2 == 0

    def test_includes_smoothstep_easing(self) -> None:
        filter_string = build_zoompan_filter(
            motion_for(AnimationPreset.SLOW_ZOOM_IN),
            frames=120, output_width=1920, output_height=1080, fps=60, supersample=3.0,
        )
        assert "(3-2*" in filter_string, "expected smoothstep easing in the expression"

    def test_single_frame_scene_does_not_divide_by_zero(self) -> None:
        filter_string = build_zoompan_filter(
            motion_for(AnimationPreset.SLOW_ZOOM_IN),
            frames=1, output_width=1920, output_height=1080, fps=60, supersample=3.0,
        )
        assert "on/0" not in filter_string

    def test_no_user_text_reaches_the_filter_string(self) -> None:
        """Filter graphs are built from numbers only; text goes through Pillow."""
        scene = Scene(
            animation_preset=AnimationPreset.SLOW_ZOOM_IN,
            title="Evil'; drop table --",
            narration="also evil",
        )
        motion = resolve_motion(scene, project_id="p", index=0)
        filter_string = build_zoompan_filter(
            motion, frames=60, output_width=1920, output_height=1080, fps=60, supersample=3.0
        )
        assert "Evil" not in filter_string
        assert "drop table" not in filter_string
