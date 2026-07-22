"""Shorts-native captions: cue rebasing, layout, capability and cache rules.

The rebasing tests are the load-bearing ones. A Short is a re-ordered subset of
the source timeline, so every caption has to be clipped to the spans that were
actually cut and moved onto the output's own clock — and getting that wrong puts
the right words over the wrong picture, which is worse than no captions at all.

None of this needs FFmpeg: the numbers come from a hand-built manifest whose
boundaries are known exactly, and the cards are drawn by Pillow.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ErrorCode
from app.shorts.captions import (
    as_text_style,
    build_caption_track,
    fit_caption_typography,
)
from app.shorts.cues import SidecarCue, rebase_cues
from app.shorts.models import (
    SHORT_HEIGHT,
    SHORT_WIDTH,
    CAPTION_PRESETS,
    ShortCaptionMode,
    ShortCaptionPreset,
    ShortCaptionStyle,
    ShortLayout,
    ShortRequest,
    resolve_caption_style,
)
from app.shorts.plan import build_plan, cache_key
from app.shorts.validate import fit_geometry
from tests.shorts_factories import build_entries, make_manifest, request_for


@pytest.fixture
def manifest(tmp_path):  # noqa: ANN001, ANN201
    entries, total = build_entries(scene_count=4, scene_duration=10.0)
    return make_manifest(tmp_path / "the-dodo_v01.mp4", entries=entries, total=total)


def cue(start: float, end: float, text: str = "words", unit: str = "scene-1") -> SidecarCue:
    return SidecarCue(
        index=1, unit_id=unit, start_seconds=start, end_seconds=end, lines=[text]
    )


def groups_for(manifest, *unit_ids: str):  # noqa: ANN001, ANN201
    return build_plan(manifest, request_for(*unit_ids)).groups


class TestRebaseOneScene:
    def test_cues_move_to_the_output_clock(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-2")
        group = groups[0]
        # A cue two seconds into the cut lands two seconds into the Short.
        cues = [cue(group.start_seconds + 2.0, group.start_seconds + 4.0)]

        rebased = rebase_cues(cues, groups)

        assert len(rebased) == 1
        assert rebased[0].start_seconds == pytest.approx(2.0)
        assert rebased[0].end_seconds == pytest.approx(4.0)
        assert rebased[0].group_index == 0

    def test_cues_outside_the_cut_are_dropped(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-2")
        group = groups[0]
        cues = [
            cue(group.start_seconds - 5.0, group.start_seconds - 3.0, "before"),
            cue(group.end_seconds + 1.0, group.end_seconds + 3.0, "after"),
        ]
        assert rebase_cues(cues, groups) == []

    def test_the_text_survives_verbatim(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-2")
        source = SidecarCue(
            index=7,
            unit_id="scene-2",
            start_seconds=groups[0].start_seconds + 1.0,
            end_seconds=groups[0].start_seconds + 3.0,
            lines=["Raphus cucullatus", "was flightless"],
        )
        rebased = rebase_cues([source], groups)
        assert rebased[0].lines == ["Raphus cucullatus", "was flightless"]
        assert rebased[0].unit_id == "scene-2"


class TestRebaseAdjacentScenes:
    def test_one_group_carries_a_cue_across_the_preserved_transition(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-1", "scene-2")
        assert len(groups) == 1, "adjacent sections must merge into one contiguous cut"
        group = groups[0]

        boundary = manifest.entry("scene-1").end_seconds
        # A cue straddling the dissolve between the two sections.
        cues = [cue(boundary - 0.8, boundary + 0.8, "across the join")]

        rebased = rebase_cues(cues, groups)

        assert len(rebased) == 1, "a cue inside one contiguous cut must not be duplicated"
        assert rebased[0].start_seconds == pytest.approx(boundary - 0.8 - group.start_seconds)
        assert rebased[0].duration_seconds == pytest.approx(1.6)

    def test_every_cue_of_both_scenes_is_kept_once(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-1", "scene-2")
        group = groups[0]
        cues = [
            cue(group.start_seconds + offset, group.start_seconds + offset + 1.0, f"c{offset}")
            for offset in (1.0, 5.0, 9.0, 13.0)
        ]
        rebased = rebase_cues(cues, groups)
        assert [entry.text for entry in rebased] == ["c1.0", "c5.0", "c9.0", "c13.0"]


class TestRebaseNonContiguousOrder:
    def test_reversed_selection_reverses_the_captions(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-3", "scene-1")
        assert len(groups) == 2

        third, first = groups[0], groups[1]
        cues = [
            cue(first.start_seconds + 1.0, first.start_seconds + 2.0, "first"),
            cue(third.start_seconds + 1.0, third.start_seconds + 2.0, "third"),
        ]

        rebased = rebase_cues(cues, groups)

        # Scene 3 plays first, so its caption is the one at t=1.
        assert [entry.text for entry in rebased] == ["third", "first"]
        assert rebased[0].start_seconds == pytest.approx(1.0)
        assert rebased[1].start_seconds == pytest.approx(third.duration_seconds + 1.0)

    def test_group_boundaries_are_where_the_cursor_advances(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-1", "scene-3")
        first, third = groups
        cues = [
            cue(first.end_seconds - 0.5, first.end_seconds - 0.2, "end of first"),
            cue(third.start_seconds + 0.1, third.start_seconds + 0.4, "start of third"),
        ]
        rebased = rebase_cues(cues, groups)
        assert rebased[0].end_seconds < first.duration_seconds
        assert rebased[1].start_seconds == pytest.approx(first.duration_seconds + 0.1)


class TestRebaseTrims:
    def test_a_cue_is_clipped_at_the_start_of_a_trim(self, manifest) -> None:  # noqa: ANN001
        entry = manifest.entry("scene-2")
        trimmed_start = entry.safe_start_seconds + 3.0
        groups = build_plan(
            manifest,
            request_for("scene-2", trims={"scene-2": (trimmed_start, None)}),
        ).groups

        # Straddles the trim: two seconds before it, two seconds after.
        cues = [cue(trimmed_start - 2.0, trimmed_start + 2.0, "clipped head")]
        rebased = rebase_cues(cues, groups)

        assert len(rebased) == 1
        assert rebased[0].start_seconds == pytest.approx(0.0)
        assert rebased[0].duration_seconds == pytest.approx(2.0)
        assert rebased[0].source_start_seconds == pytest.approx(trimmed_start)

    def test_a_cue_is_clipped_at_the_end_of_a_trim(self, manifest) -> None:  # noqa: ANN001
        entry = manifest.entry("scene-2")
        trimmed_end = entry.safe_end_seconds - 3.0
        groups = build_plan(
            manifest,
            request_for("scene-2", trims={"scene-2": (None, trimmed_end)}),
        ).groups
        group = groups[0]

        cues = [cue(trimmed_end - 1.5, trimmed_end + 4.0, "clipped tail")]
        rebased = rebase_cues(cues, groups)

        assert len(rebased) == 1
        assert rebased[0].end_seconds == pytest.approx(group.duration_seconds)
        assert rebased[0].duration_seconds == pytest.approx(1.5)

    def test_a_cue_entirely_inside_the_trimmed_away_part_is_gone(self, manifest) -> None:  # noqa: ANN001
        entry = manifest.entry("scene-2")
        trimmed_start = entry.safe_start_seconds + 4.0
        groups = build_plan(
            manifest,
            request_for("scene-2", trims={"scene-2": (trimmed_start, None)}),
        ).groups
        cues = [cue(entry.safe_start_seconds + 0.5, entry.safe_start_seconds + 2.0, "gone")]
        assert rebase_cues(cues, groups) == []


class TestRebaseOverlapAndSlivers:
    def test_overlapping_cues_are_both_kept(self, manifest) -> None:  # noqa: ANN001
        """Two sections overlap during a dissolve, so their captions can too.

        Dropping one would lose a line of narration that really is being spoken;
        both are kept and alpha-composite over each other the way the pictures do.
        """
        groups = groups_for(manifest, "scene-1", "scene-2")
        boundary = manifest.entry("scene-1").end_seconds
        cues = [
            cue(boundary - 1.0, boundary + 0.15, "outgoing", unit="scene-1"),
            cue(boundary - 0.15, boundary + 1.0, "incoming", unit="scene-2"),
        ]

        rebased = rebase_cues(cues, groups)

        assert [entry.text for entry in rebased] == ["outgoing", "incoming"]
        assert rebased[1].start_seconds < rebased[0].end_seconds, "the overlap is preserved"

    def test_a_sliver_left_by_a_cut_is_discarded(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-2")
        group = groups[0]
        # 30 ms of a cue survives the cut: under two frames, a flicker.
        cues = [cue(group.start_seconds - 4.0, group.start_seconds + 0.03, "sliver")]
        assert rebase_cues(cues, groups) == []

    def test_a_zero_length_cue_is_discarded(self, manifest) -> None:  # noqa: ANN001
        groups = groups_for(manifest, "scene-2")
        at = groups[0].start_seconds + 2.0
        assert rebase_cues([cue(at, at, "empty")], groups) == []

    def test_no_cues_at_all_is_not_an_error(self, manifest) -> None:  # noqa: ANN001
        assert rebase_cues([], groups_for(manifest, "scene-2")) == []


class TestCaptionStyleResolution:
    def test_the_default_is_the_standard_preset(self) -> None:
        assert resolve_caption_style(None) == CAPTION_PRESETS[ShortCaptionPreset.STANDARD]

    def test_a_bare_preset_brings_that_preset_s_values(self) -> None:
        resolved = resolve_caption_style(ShortCaptionStyle(preset=ShortCaptionPreset.LARGE))
        assert resolved.font_size == CAPTION_PRESETS[ShortCaptionPreset.LARGE].font_size
        assert resolved.font_size > CAPTION_PRESETS[ShortCaptionPreset.STANDARD].font_size

    def test_an_explicit_field_wins_over_its_preset(self) -> None:
        resolved = resolve_caption_style(
            ShortCaptionStyle(preset=ShortCaptionPreset.LARGE, font_size=44)
        )
        assert resolved.font_size == 44
        # ...and the rest still comes from the preset.
        assert resolved.safe_bottom_inset == (
            CAPTION_PRESETS[ShortCaptionPreset.LARGE].safe_bottom_inset
        )

    def test_out_of_range_values_are_refused_not_clamped(self) -> None:
        with pytest.raises(ValueError):
            ShortCaptionStyle(font_size=900)
        with pytest.raises(ValueError):
            ShortCaptionStyle(max_lines=9)
        with pytest.raises(ValueError):
            ShortCaptionStyle(color="rgb(1,2,3)")

    def test_defaults_match_the_documented_shorts_geometry(self) -> None:
        style = resolve_caption_style(None)
        assert style.max_lines == 2
        assert 54 <= style.font_size <= 64
        assert 0.82 <= style.max_width_ratio <= 0.86
        assert 340 <= style.safe_bottom_inset <= 420
        assert style.box and style.box_opacity > 0


class TestCaptionLayout:
    """Where the drawn card actually lands on a 1080x1920 canvas."""

    def track(self, cues, style=None, tmp_path=None):  # noqa: ANN001, ANN201
        return build_caption_track(
            rebase_cues(cues, _one_group(len(cues) * 4 + 10)),
            style or resolve_caption_style(None),
            canvas_width=SHORT_WIDTH,
            canvas_height=SHORT_HEIGHT,
            output_dir=tmp_path,
        )

    def test_the_card_sits_bottom_centre(self, tmp_path) -> None:  # noqa: ANN001
        track = self.track([cue(1.0, 3.0, "A dodo stood a metre tall.")], tmp_path=tmp_path)
        card = track.cards[0].card
        centre = card.box_x + card.box_width // 2
        assert abs(centre - SHORT_WIDTH // 2) <= 1, "the box is centred horizontally"
        assert card.box_bottom == SHORT_HEIGHT - resolve_caption_style(None).safe_bottom_inset

    def test_the_card_clears_the_16_by_9_picture(self, tmp_path) -> None:  # noqa: ANN001
        """The whole point: captions live on the canvas, not over the film."""
        geometry = fit_geometry(1920, 1080, SHORT_WIDTH, SHORT_HEIGHT)
        picture_bottom = geometry.offset_y + geometry.inner_height

        for preset in ShortCaptionPreset:
            track = self.track(
                [cue(1.0, 3.0, "A dodo stood about a metre tall and could not fly.")],
                style=resolve_caption_style(ShortCaptionStyle(preset=preset)),
                tmp_path=tmp_path,
            )
            card = track.cards[0].card
            assert card.box_y >= picture_bottom, (
                f"{preset.value} captions overlap the picture "
                f"(box top {card.box_y}, picture ends {picture_bottom})"
            )

    def test_the_card_stays_inside_the_canvas(self, tmp_path) -> None:  # noqa: ANN001
        track = self.track(
            [cue(1.0, 3.0, "Sailors called them walghvogels, meaning tasteless birds.")],
            tmp_path=tmp_path,
        )
        card = track.cards[0].card
        assert card.box_x >= 0
        assert card.box_x + card.box_width <= SHORT_WIDTH
        assert card.box_bottom <= SHORT_HEIGHT

    def test_a_long_cue_is_fitted_rather_than_spilling_to_a_third_line(self) -> None:
        style = resolve_caption_style(None)
        long_text = (
            "The dodo was a flightless bird endemic to Mauritius, east of Madagascar "
            "in the Indian Ocean."
        )
        size, wrapped = fit_caption_typography([long_text], style, canvas_width=SHORT_WIDTH)
        assert len(wrapped[long_text]) <= style.max_lines
        assert size <= style.font_size
        assert size >= int(style.font_size * style.min_font_scale)

    def test_one_size_is_used_for_every_cue_in_the_short(self) -> None:
        style = resolve_caption_style(None)
        texts = ["Short.", "A considerably longer caption than the one before it, by far."]
        size, wrapped = fit_caption_typography(texts, style, canvas_width=SHORT_WIDTH)
        assert all(len(lines) <= style.max_lines for lines in wrapped.values())
        # One returned size, applied to both — captions must not change size mid-clip.
        assert isinstance(size, int)

    def test_the_style_translation_carries_every_value(self) -> None:
        style = resolve_caption_style(ShortCaptionStyle(preset=ShortCaptionPreset.LARGE))
        text_style = as_text_style(style)
        assert text_style.size == style.font_size
        assert text_style.box_opacity == style.box_opacity
        assert text_style.outline_width == style.outline_width
        assert text_style.max_width_ratio == style.max_width_ratio
        # Fading is applied over time by the compositor, never baked into a card.
        assert text_style.fade_in_seconds == 0.0


class TestCacheKey:
    """Identical requests reuse; anything that changes pixels does not."""

    def base(self, manifest):  # noqa: ANN001, ANN201
        plan = build_plan(manifest, request_for("scene-1", "scene-2"))
        return plan

    def test_the_legacy_key_is_unchanged_by_the_captions_feature(self, manifest) -> None:  # noqa: ANN001
        """A request that does not mention captions must hash as it always did.

        This is what keeps every Short already on disk matching its own request
        instead of being silently re-rendered under a new name.
        """
        plan = self.base(manifest)
        legacy = cache_key(manifest, plan, ShortLayout())
        explicit = cache_key(
            manifest,
            plan,
            ShortLayout(),
            caption_mode=ShortCaptionMode.SOURCE_BURNED_IN,
            caption_style=resolve_caption_style(None),
        )
        assert legacy == explicit

    def test_the_same_request_twice_gives_the_same_key(self, manifest) -> None:  # noqa: ANN001
        request = ShortRequest(
            source_render_id="render0001",
            segments=request_for("scene-1").segments,
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
        )
        assert build_plan(manifest, request).cache_key == build_plan(manifest, request).cache_key

    def test_caption_mode_changes_the_key(self, manifest) -> None:  # noqa: ANN001
        plan = self.base(manifest)
        keys = {
            mode: cache_key(manifest, plan, ShortLayout(), caption_mode=mode)
            for mode in ShortCaptionMode
        }
        assert len(set(keys.values())) == 3, "each caption mode is a different Short"

    def test_caption_style_changes_the_key(self, manifest) -> None:  # noqa: ANN001
        plan = self.base(manifest)
        keys = {
            preset: cache_key(
                manifest,
                plan,
                ShortLayout(),
                caption_mode=ShortCaptionMode.SHORTS_NATIVE,
                caption_style=resolve_caption_style(ShortCaptionStyle(preset=preset)),
            )
            for preset in ShortCaptionPreset
        }
        assert len(set(keys.values())) == len(ShortCaptionPreset)

    def test_a_single_style_field_changes_the_key(self, manifest) -> None:  # noqa: ANN001
        plan = self.base(manifest)
        default = cache_key(
            manifest, plan, ShortLayout(),
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
            caption_style=resolve_caption_style(None),
        )
        nudged = cache_key(
            manifest, plan, ShortLayout(),
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
            caption_style=resolve_caption_style(ShortCaptionStyle(safe_bottom_inset=300)),
        )
        assert default != nudged

    def test_style_is_irrelevant_when_nothing_is_drawn(self, manifest) -> None:  # noqa: ANN001
        """'off' produces the same pixels whatever the style says."""
        plan = self.base(manifest)
        first = cache_key(
            manifest, plan, ShortLayout(),
            caption_mode=ShortCaptionMode.OFF,
            caption_style=resolve_caption_style(None),
        )
        second = cache_key(
            manifest, plan, ShortLayout(),
            caption_mode=ShortCaptionMode.OFF,
            caption_style=resolve_caption_style(ShortCaptionStyle(font_size=120)),
        )
        assert first == second


class TestRequestCompatibility:
    def test_a_request_without_caption_fields_is_the_legacy_mode(self) -> None:
        request = ShortRequest.model_validate(
            {"sourceRenderId": "render0001", "segments": [{"unitId": "scene-1"}]}
        )
        assert request.caption_mode is ShortCaptionMode.SOURCE_BURNED_IN
        assert request.caption_style is None

    def test_a_request_with_only_a_preset_resolves_fully(self) -> None:
        request = ShortRequest.model_validate(
            {
                "sourceRenderId": "render0001",
                "segments": [{"unitId": "scene-1"}],
                "captionMode": "shorts-native",
                "captionStyle": {"preset": "compact"},
            }
        )
        style = request.resolved_caption_style()
        assert style.preset is ShortCaptionPreset.COMPACT
        assert style.font_size == CAPTION_PRESETS[ShortCaptionPreset.COMPACT].font_size

    def test_an_unknown_caption_mode_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ShortRequest.model_validate(
                {
                    "sourceRenderId": "render0001",
                    "segments": [{"unitId": "scene-1"}],
                    "captionMode": "burn-them-again",
                }
            )


def _one_group(duration: float):  # noqa: ANN202
    """A single group covering 0..duration, for layout tests that need no cutting."""
    from app.shorts.models import ShortGroupPlan

    return [
        ShortGroupPlan(
            index=0,
            start_seconds=0.0,
            end_seconds=duration,
            duration_seconds=duration,
            unit_ids=["scene-1"],
            numbers=[1],
        )
    ]


def test_error_codes_exist_for_every_caption_failure() -> None:
    """The two failure modes the UI branches on must be distinguishable."""
    assert ErrorCode.SHORT_CAPTIONS_UNAVAILABLE.value == "short_captions_unavailable"
    assert ErrorCode.SHORT_CLEAN_SOURCE_STALE.value == "short_clean_source_stale"
    assert issubclass(AppError, Exception)
