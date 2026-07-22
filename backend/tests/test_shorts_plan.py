"""Selection, trimming, grouping and cache-key behaviour.

These are the rules that decide which frames end up in a Short, so they are
tested against a hand-built manifest whose numbers are known exactly rather than
against whatever a render happened to produce.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ErrorCode
from app.shorts.models import (
    MAX_SHORT_SECONDS,
    MIN_CLIP_SECONDS,
    ShortLayout,
    ShortRequest,
    ShortSegmentRequest,
)
from app.shorts.plan import build_plan, cache_key
from tests.shorts_factories import build_entries, make_manifest, request_for


@pytest.fixture
def manifest(tmp_path):  # noqa: ANN001, ANN201
    entries, total = build_entries(scene_count=4, scene_duration=10.0)
    return make_manifest(tmp_path / "the-dodo_v01.mp4", entries=entries, total=total)


class TestNumbering:
    def test_intro_is_zero_scenes_count_from_one_outro_is_last(self, manifest) -> None:  # noqa: ANN001
        numbers = [(e.kind, e.number) for e in manifest.entries]
        assert numbers == [
            ("intro", 0),
            ("scene", 1),
            ("scene", 2),
            ("scene", 3),
            ("scene", 4),
            ("outro", 5),
        ]

    def test_scenes_still_start_at_one_without_an_intro(self, tmp_path) -> None:  # noqa: ANN001
        entries, total = build_entries(scene_count=3, with_intro=False)
        built = make_manifest(tmp_path / "x.mp4", entries=entries, total=total)
        assert [e.number for e in built.entries] == [1, 2, 3, 4]
        assert built.entries[-1].kind == "outro"


class TestSafeBounds:
    def test_transition_overlap_is_excluded_at_both_ends(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        assert scene is not None
        # 0.5s dissolve on each side of a 10s section.
        assert scene.safe_start_seconds == pytest.approx(scene.start_seconds + 0.5)
        assert scene.safe_end_seconds == pytest.approx(scene.end_seconds - 0.5)
        assert scene.safe_duration_seconds == pytest.approx(9.0)

    def test_first_section_has_no_incoming_overlap(self, manifest) -> None:  # noqa: ANN001
        intro = manifest.entry("intro")
        assert intro is not None
        assert intro.safe_start_seconds == pytest.approx(intro.start_seconds)

    def test_safe_end_of_one_section_meets_the_next_safe_start_at_the_transition(
        self, manifest
    ) -> None:  # noqa: ANN001
        first, second = manifest.entries[1], manifest.entries[2]
        # The gap between them is exactly the transition: those frames belong to
        # both sections and are only used when the two are cut together.
        gap = second.safe_start_seconds - first.safe_end_seconds
        assert gap == pytest.approx(first.transition_duration_seconds)


class TestSelectionOrder:
    def test_selection_order_is_preserved(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-4", "scene-1"))
        assert [s.number for s in plan.segments] == [4, 1]
        assert [g.numbers for g in plan.groups] == [[4], [1]]
        # The later section really is cut first.
        assert plan.groups[0].start_seconds > plan.groups[1].start_seconds

    def test_adjacent_selection_becomes_one_contiguous_cut(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-2", "scene-3"))
        assert len(plan.groups) == 1
        group = plan.groups[0]
        assert group.numbers == [2, 3]
        assert group.preserved_transitions == 1
        # The cut spans from scene 2's safe start to scene 3's safe end, so the
        # dissolve between them is inside the cut and survives untouched.
        assert group.start_seconds == pytest.approx(manifest.entry("scene-2").safe_start_seconds)
        assert group.end_seconds == pytest.approx(manifest.entry("scene-3").safe_end_seconds)

    def test_non_adjacent_selection_stays_two_cuts(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-1", "scene-3"))
        assert [g.numbers for g in plan.groups] == [[1], [3]]
        assert all(g.preserved_transitions == 0 for g in plan.groups)

    def test_reverse_adjacent_order_is_not_merged(self, manifest) -> None:  # noqa: ANN001
        # 3 then 2 is not contiguous *as played*, so it must stay two cuts.
        plan = build_plan(manifest, request_for("scene-3", "scene-2"))
        assert [g.numbers for g in plan.groups] == [[3], [2]]

    def test_intro_and_first_scene_merge(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("intro", "scene-1"))
        assert len(plan.groups) == 1
        assert plan.groups[0].numbers == [0, 1]

    def test_a_trim_at_the_join_splits_the_group(self, manifest) -> None:  # noqa: ANN001
        scene2 = manifest.entry("scene-2")
        plan = build_plan(
            manifest,
            request_for(
                "scene-2", "scene-3",
                trims={"scene-2": (None, scene2.safe_end_seconds - 2.0)},
            ),
        )
        # Trimming the join means the dissolve cannot be carried, so the two
        # become separate cuts joined by a hard cut rather than half a fade.
        assert [g.numbers for g in plan.groups] == [[2], [3]]

    def test_total_of_a_merged_group_includes_the_preserved_transition(
        self, manifest
    ) -> None:  # noqa: ANN001
        merged = build_plan(manifest, request_for("scene-2", "scene-3"))
        split = build_plan(manifest, request_for("scene-3", "scene-2"))
        # The merged cut is exactly one transition longer, because it contains
        # the overlap that the split version drops.
        assert merged.total_duration_seconds == pytest.approx(
            split.total_duration_seconds + 0.5
        )


class TestValidation:
    def test_empty_selection_is_rejected(self, manifest) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc:
            build_plan(manifest, ShortRequest(source_render_id="render0001", segments=[]))
        assert exc.value.code is ErrorCode.SHORT_INVALID_SELECTION

    def test_unknown_section_is_rejected_by_name(self, manifest) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc:
            build_plan(manifest, request_for("scene-99"))
        assert exc.value.code is ErrorCode.SHORT_INVALID_SELECTION
        assert "scene-99" in exc.value.message

    def test_duplicate_section_is_rejected(self, manifest) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc:
            build_plan(manifest, request_for("scene-2", "scene-2"))
        assert exc.value.code is ErrorCode.SHORT_INVALID_SELECTION

    def test_trim_outside_the_safe_range_is_rejected(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        with pytest.raises(AppError) as exc:
            build_plan(
                manifest,
                request_for("scene-2", trims={"scene-2": (scene.start_seconds, None)}),
            )
        assert exc.value.code is ErrorCode.SHORT_INVALID_TRIM
        assert "transitions" in (exc.value.details or "")

    def test_trim_past_the_safe_end_is_rejected(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        with pytest.raises(AppError) as exc:
            build_plan(
                manifest,
                request_for("scene-2", trims={"scene-2": (None, scene.end_seconds)}),
            )
        assert exc.value.code is ErrorCode.SHORT_INVALID_TRIM

    def test_inverted_trim_is_rejected(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        with pytest.raises(AppError) as exc:
            build_plan(
                manifest,
                request_for(
                    "scene-2",
                    trims={"scene-2": (scene.safe_end_seconds - 1.0, scene.safe_start_seconds + 1.0)},
                ),
            )
        assert exc.value.code is ErrorCode.SHORT_INVALID_TRIM

    def test_clip_under_the_minimum_is_rejected(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        with pytest.raises(AppError) as exc:
            build_plan(
                manifest,
                request_for(
                    "scene-2",
                    trims={
                        "scene-2": (
                            scene.safe_start_seconds,
                            scene.safe_start_seconds + MIN_CLIP_SECONDS / 2,
                        )
                    },
                ),
            )
        assert exc.value.code is ErrorCode.SHORT_INVALID_TRIM

    def test_over_three_minutes_is_blocked(self, tmp_path) -> None:  # noqa: ANN001
        entries, total = build_entries(scene_count=25, scene_duration=20.0)
        big = make_manifest(tmp_path / "long.mp4", entries=entries, total=total)
        selection = request_for(*[f"scene-{i}" for i in range(1, 20)])
        with pytest.raises(AppError) as exc:
            build_plan(big, selection)
        assert exc.value.code is ErrorCode.SHORT_TOO_LONG
        assert str(int(MAX_SHORT_SECONDS)) in exc.value.message


class TestWarnings:
    def test_a_minute_long_short_warns_about_content_id(self, tmp_path) -> None:  # noqa: ANN001
        entries, total = build_entries(scene_count=10, scene_duration=20.0)
        built = make_manifest(tmp_path / "m.mp4", entries=entries, total=total)
        plan = build_plan(built, request_for("scene-1", "scene-2", "scene-3", "scene-4"))
        assert plan.total_duration_seconds > 60
        assert any("Content ID" in w for w in plan.warnings)

    def test_a_short_selection_warns_it_is_under_the_band(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-2"))
        assert plan.total_duration_seconds < 25
        assert any("recommended band" in w for w in plan.warnings)

    def test_no_content_id_warning_under_a_minute(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-1", "scene-2", "scene-3"))
        assert plan.total_duration_seconds < 60
        assert not any("Content ID" in w for w in plan.warnings)


class TestCacheKey:
    def test_identical_requests_produce_identical_keys(self, manifest) -> None:  # noqa: ANN001
        first = build_plan(manifest, request_for("scene-1", "scene-3"))
        second = build_plan(manifest, request_for("scene-1", "scene-3"))
        assert first.cache_key == second.cache_key
        assert len(first.cache_key) == 16

    def test_a_different_order_produces_a_different_key(self, manifest) -> None:  # noqa: ANN001
        forward = build_plan(manifest, request_for("scene-1", "scene-3"))
        reverse = build_plan(manifest, request_for("scene-3", "scene-1"))
        assert forward.cache_key != reverse.cache_key

    def test_a_trim_changes_the_key(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        plain = build_plan(manifest, request_for("scene-2"))
        trimmed = build_plan(
            manifest,
            request_for("scene-2", trims={"scene-2": (scene.safe_start_seconds + 1.0, None)}),
        )
        assert plain.cache_key != trimmed.cache_key

    def test_a_changed_source_invalidates_the_key(self, tmp_path, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-2"))
        entries, total = build_entries(scene_count=4, scene_duration=10.0)
        rerendered = make_manifest(
            tmp_path / "the-dodo_v02.mp4", entries=entries, total=total, checksum="b" * 64
        )
        again = build_plan(rerendered, request_for("scene-2"))
        assert plan.cache_key != again.cache_key

    def test_layout_changes_the_key(self, manifest) -> None:  # noqa: ANN001
        plan = build_plan(manifest, request_for("scene-2"))
        other = cache_key(manifest, plan, ShortLayout(background_color="#101010"))
        assert other != plan.cache_key

    def test_millisecond_noise_does_not_change_the_key(self, manifest) -> None:  # noqa: ANN001
        scene = manifest.entry("scene-2")
        request = ShortRequest(
            source_render_id="render0001",
            segments=[
                ShortSegmentRequest(
                    unit_id="scene-2",
                    start_seconds=scene.safe_start_seconds + 0.0001,
                    end_seconds=scene.safe_end_seconds,
                )
            ],
        )
        assert build_plan(manifest, request).cache_key == build_plan(
            manifest, request_for("scene-2")
        ).cache_key
