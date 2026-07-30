"""The Short's opening hook: authoring rules, drawing, placement and caching.

The two claims worth proving are that the hook lands on the black band *above*
the picture (never over the film, never over the captions), and that a Short
without one is identical to the Short it was before hooks existed — same cache
key, same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.project import PlannedShort, ShortHook, ShortsPlan
from app.shorts.hooks import build_hook_card, fit_hook_size, overlay_steps
from app.shorts.models import DEFAULT_HOOK_STYLE, SHORT_HEIGHT, SHORT_WIDTH

HOOK = ShortHook(lines=["When he died,", "the species ended"])


class TestAuthoring:
    def test_a_planned_short_always_has_a_hook_block(self) -> None:
        planned = PlannedShort(id="last-one", sections=[{"kind": "outro"}])  # type: ignore[list-item]
        assert planned.hook.lines == []
        assert planned.hook.is_visible is False

    def test_more_than_two_lines_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ShortHook(lines=["one", "two", "three"])

    def test_a_line_too_long_for_a_phone_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="42 characters"):
            ShortHook(lines=["x" * 43])

    def test_whitespace_is_tidied_and_blank_lines_dropped(self) -> None:
        hook = ShortHook(lines=["  When   he  died, ", "   "])
        assert hook.lines == ["When he died,"]
        assert hook.text == "When he died,"

    def test_a_hook_is_off_when_it_has_nothing_to_say(self) -> None:
        assert ShortHook(lines=[]).is_visible is False
        assert ShortHook(lines=["Something"], enabled=False).is_visible is False

    def test_the_house_timing_is_the_first_beat(self) -> None:
        hook = ShortHook()
        assert hook.start_seconds == 0.0
        assert 2.0 <= hook.duration_seconds <= 2.5

    def test_a_plan_round_trips_through_json_with_its_hooks(self) -> None:
        plan = ShortsPlan(
            shorts=[
                PlannedShort(
                    id="last-one",
                    sections=[{"kind": "scene", "number": 9}],  # type: ignore[list-item]
                    hook=HOOK,
                )
            ]
        )
        restored = ShortsPlan.model_validate_json(plan.model_dump_json())
        assert restored.shorts[0].hook.lines == HOOK.lines


class TestDrawing:
    def test_it_sits_near_eye_level_not_at_the_top(self, tmp_path: Path) -> None:
        card = build_hook_card(
            HOOK, DEFAULT_HOOK_STYLE,
            canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
            total_duration_seconds=30.0, output_dir=tmp_path,
        )
        assert card is not None
        assert card.lead_card is not None
        combined_top = min(card.lead_card.box_y, card.card.box_y)
        combined_bottom = max(card.lead_card.box_bottom, card.card.box_bottom)
        combined_centre = (combined_top + combined_bottom) / 2
        assert SHORT_HEIGHT * 0.38 <= combined_centre <= SHORT_HEIGHT * 0.55
        assert combined_bottom < SHORT_HEIGHT * 0.62, "keep clear of lower captions and controls"

    def test_it_fits_inside_the_canvas(self, tmp_path: Path) -> None:
        card = build_hook_card(
            HOOK, DEFAULT_HOOK_STYLE,
            canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
            total_duration_seconds=30.0, output_dir=tmp_path,
        )
        assert card is not None
        assert card.card.box_x >= 0
        assert card.card.box_x + card.card.box_width <= SHORT_WIDTH

    def test_the_authored_line_break_is_kept(self, tmp_path: Path) -> None:
        card = build_hook_card(
            HOOK, DEFAULT_HOOK_STYLE,
            canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
            total_duration_seconds=30.0, output_dir=tmp_path,
        )
        assert card is not None
        assert card.lead_card is not None
        assert card.lead_card.text == "WHEN HE DIED,"
        assert card.card.text == "THE SPECIES ENDED"
        assert card.impact_start_seconds > card.start_seconds

    def test_long_lines_shrink_rather_than_re_break(self) -> None:
        short = fit_hook_size(["GONE"], DEFAULT_HOOK_STYLE, canvas_width=SHORT_WIDTH)
        long = fit_hook_size(
            ["THIS GIANT DISAPPEARED FOREVER"], DEFAULT_HOOK_STYLE, canvas_width=SHORT_WIDTH
        )
        assert short == DEFAULT_HOOK_STYLE.font_size
        assert long < short
        floor = DEFAULT_HOOK_STYLE.font_size * DEFAULT_HOOK_STYLE.min_font_scale
        assert long >= floor - 2

    def test_nothing_is_drawn_without_a_hook(self, tmp_path: Path) -> None:
        assert (
            build_hook_card(
                ShortHook(lines=[]), DEFAULT_HOOK_STYLE,
                canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
                total_duration_seconds=30.0, output_dir=tmp_path,
            )
            is None
        )
        assert list(tmp_path.iterdir()) == [], "no card should have been drawn"

    def test_a_window_outside_the_short_draws_nothing(self, tmp_path: Path) -> None:
        late = ShortHook(lines=["TOO LATE"], start_seconds=9.0, duration_seconds=1.4)
        assert (
            build_hook_card(
                late, DEFAULT_HOOK_STYLE,
                canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
                total_duration_seconds=4.0, output_dir=tmp_path,
            )
            is None
        )

    def test_it_is_clipped_to_the_short_it_belongs_to(self, tmp_path: Path) -> None:
        hook = ShortHook(lines=["GONE"], start_seconds=0.0, duration_seconds=6.0)
        card = build_hook_card(
            hook, DEFAULT_HOOK_STYLE,
            canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
            total_duration_seconds=3.0, output_dir=tmp_path,
        )
        assert card is not None
        assert card.end_seconds == pytest.approx(3.0)


class TestFiltergraph:
    def test_the_words_never_reach_the_graph(self, tmp_path: Path) -> None:
        card = build_hook_card(
            HOOK, DEFAULT_HOOK_STYLE,
            canvas_width=SHORT_WIDTH, canvas_height=SHORT_HEIGHT,
            total_duration_seconds=30.0, output_dir=tmp_path,
        )
        assert card is not None
        steps, final = overlay_steps(
            card, style=DEFAULT_HOOK_STYLE, current="canvas", add_input=lambda _p: 3
        )
        graph = ";".join(steps)
        assert "SPECIES" not in graph.upper().replace("HOOKCARD", "")
        assert "drawtext" not in graph
        assert "enable='between(t,0.000,2.200)'" in graph
        assert "enable='between(t,0.420,2.200)'" in graph
        assert "if(lt(t,0.620)" in graph
        assert final == "hooked"


class TestCaching:
    """A hook changes the pixels, so it must change the Short's cache key."""

    def _key(self, hook: ShortHook | None) -> str:
        from tests.shorts_factories import make_manifest

        from app.shorts.models import ShortGroupPlan, ShortLayout, ShortPlan
        from app.shorts.plan import cache_key

        manifest = make_manifest(Path("the-dodo.mp4"))
        plan = ShortPlan(
            groups=[
                ShortGroupPlan(
                    index=0, start_seconds=0.0, end_seconds=30.0,
                    duration_seconds=30.0, unit_ids=["scene-1"],
                )
            ],
            total_duration_seconds=30.0,
        )
        return cache_key(manifest, plan, ShortLayout(), hook=hook)

    def test_no_hook_hashes_exactly_as_it_always_did(self) -> None:
        assert self._key(None) == self._key(ShortHook(lines=[]))

    def test_a_different_hook_is_a_different_short(self) -> None:
        assert self._key(HOOK) != self._key(None)
        assert self._key(HOOK) != self._key(ShortHook(lines=["Something else"]))

    def test_retiming_the_same_words_is_a_different_short(self) -> None:
        later = ShortHook(lines=HOOK.lines, start_seconds=0.5)
        assert self._key(later) != self._key(HOOK)
