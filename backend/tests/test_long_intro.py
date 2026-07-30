"""The branded long-video opening: schema, drawing, and where it may appear.

The load-bearing claim of this feature is a negative one — the opening belongs
to the long video and to nothing else — so most of what is asserted here is
about the *absence* of the intro from the Shorts clean master, and about the
timeline being untouched by it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.models.enums import IntroStyle
from app.models.migrations import migrate
from app.models.project import SCHEMA_VERSION, Animal, LongIntro, Project
from app.render.clean_master import clean_master_project, plan_clean_master
from app.render.intro import build_intro_track, overlay_steps
from app.render.sfx import build_long_intro_sfx
from app.timing.schedule import build_timeline


def make_project(**kwargs: object) -> Project:
    project = Project(name="Dodo", **kwargs)  # type: ignore[arg-type]
    project.animal = Animal(common_name="Dodo", scientific_name="Raphus cucullatus")
    return project


class TestSchema:
    def test_a_new_project_opens_with_the_channel_intro(self) -> None:
        intro = make_project().long_intro
        assert intro.enabled is True
        assert intro.intro_style is IntroStyle.TYPEWRITER_STAMP
        assert intro.stamp_text == "EXTINCT"
        # Reveal, identify, stamp, and a readable hold.
        assert 4.0 <= intro.duration <= 4.5

    def test_blank_titles_resolve_to_the_animal(self) -> None:
        project = make_project()
        resolved = project.resolved_long_intro()
        assert resolved.primary_title == "Dodo"
        assert resolved.secondary_title == "Raphus cucullatus"

    def test_authored_titles_are_never_overwritten(self) -> None:
        project = make_project()
        project.long_intro.primary_title = "THE LAST DODO"
        project.long_intro.secondary_title = ""
        resolved = project.resolved_long_intro()
        assert resolved.primary_title == "THE LAST DODO"
        assert resolved.secondary_title == "Raphus cucullatus"

    def test_a_project_with_no_animal_and_no_titles_draws_nothing(self) -> None:
        project = Project(name="Untitled")
        project.long_intro.stamp_text = ""
        assert project.has_long_intro is False

    def test_the_stamp_cannot_land_after_the_intro_has_ended(self) -> None:
        with pytest.raises(ValueError, match="stampAt"):
            LongIntro(duration=2.0, stamp_at=3.0)

    def test_typing_cannot_outlast_the_intro(self) -> None:
        with pytest.raises(ValueError, match="typewriterDuration"):
            LongIntro(duration=1.0, typewriter_duration=2.0)

    def test_the_wire_names_are_the_documented_ones(self) -> None:
        payload = json.loads(LongIntro().model_dump_json())
        assert {
            "introStyle", "primaryTitle", "secondaryTitle", "stampText",
            "duration", "typewriterDuration", "stampAt",
        } <= set(payload)


class TestMigration:
    def test_a_v2_project_gains_an_opening_and_empty_hooks(self) -> None:
        raw = {
            "schemaVersion": 2,
            "name": "Dodo",
            "shortsPlan": {
                "version": 1,
                "shorts": [
                    {"id": "last-one", "sections": [{"kind": "scene", "number": 3}]}
                ],
            },
        }
        migrated = migrate(raw)
        assert migrated["schemaVersion"] == SCHEMA_VERSION
        assert migrated["longIntro"]["enabled"] is True
        assert migrated["shortsPlan"]["shorts"][0]["hook"] == {"lines": []}

    def test_a_migrated_project_still_validates_and_keeps_its_work(self) -> None:
        raw = json.loads(make_project().model_dump_json())
        raw["schemaVersion"] = 1
        # A real v1 file has neither of the fields the two migrations add.
        raw.pop("longIntro")
        raw["export"].pop("prepareCleanMasterForShorts")
        raw["scenes"] = []

        project = Project.model_validate(migrate(raw))
        assert project.schema_version == SCHEMA_VERSION
        assert project.long_intro.enabled is True
        assert project.animal.common_name == "Dodo"
        # v1 -> v2 still opts an old project out of the extra clean-master pass.
        assert project.export.prepare_clean_master_for_shorts is False

    def test_an_authored_opening_survives_the_migration(self) -> None:
        raw = {"schemaVersion": 2, "name": "Dodo", "longIntro": {"enabled": False}}
        assert migrate(raw)["longIntro"] == {"enabled": False}

    def test_v3_house_timings_are_repaired_but_authored_timings_survive(self) -> None:
        old_default = migrate(
            {
                "schemaVersion": 3,
                "longIntro": {
                    "duration": 2.6,
                    "typewriterDuration": 1.3,
                    "stampAt": 1.7,
                },
                "shortsPlan": {
                    "shorts": [{"hook": {"lines": ["Gone."], "durationSeconds": 1.4}}]
                },
            }
        )
        assert old_default["longIntro"]["duration"] == 4.2
        assert old_default["shortsPlan"]["shorts"][0]["hook"]["durationSeconds"] == 2.2

        authored = migrate(
            {
                "schemaVersion": 3,
                "longIntro": {
                    "duration": 3.5,
                    "typewriterDuration": 1.0,
                    "stampAt": 2.0,
                },
                "shortsPlan": {
                    "shorts": [{"hook": {"lines": ["Gone."], "durationSeconds": 3.0}}]
                },
            }
        )
        assert authored["longIntro"]["duration"] == 3.5
        assert authored["shortsPlan"]["shorts"][0]["hook"]["durationSeconds"] == 3.0


class TestDrawing:
    @pytest.fixture
    def cards(self, tmp_path: Path) -> Path:
        return tmp_path / "intro"

    def test_it_draws_one_card_per_typed_state_then_holds_and_stamps(self, cards: Path) -> None:
        track = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter",
            width=1920,
            height=1080,
            output_dir=cards,
        )
        assert not track.is_empty
        # One card per revealed letter, a five-step scientific-name fade, an
        # eased eight-step stamp landing, and the holds between/after them.
        assert len(track.cards) == len("DODO") + 5 + 1 + 8 + 1
        assert track.cards[0].start_seconds == 0.0
        assert track.cards[-1].end_seconds == pytest.approx(track.duration_seconds)

    def test_the_windows_are_contiguous_and_never_overlap(self, cards: Path) -> None:
        track = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        for earlier, later in zip(track.cards, track.cards[1:]):
            assert earlier.end_seconds == pytest.approx(later.start_seconds, abs=1e-3)
            assert earlier.end_seconds > earlier.start_seconds

    def test_every_card_is_a_full_frame_rgba_png(self, cards: Path) -> None:
        track = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        for card in track.cards:
            with Image.open(card.path) as image:
                assert image.mode == "RGBA"
                assert image.size == (1920, 1080)

    def test_a_second_build_redraws_nothing(self, cards: Path) -> None:
        project = make_project()
        first = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        stamps = {card.path: card.path.stat().st_mtime_ns for card in first.cards}
        second = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        assert second.digest == first.digest
        assert {card.path: card.path.stat().st_mtime_ns for card in second.cards} == stamps

    def test_a_different_animal_produces_a_different_track(self, cards: Path) -> None:
        dodo = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        other = make_project()
        other.animal = Animal(common_name="Thylacine", scientific_name="Thylacinus cynocephalus")
        thylacine = build_intro_track(
            other.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        assert thylacine.digest != dodo.digest

    def test_plain_title_style_neither_types_nor_stamps(self, cards: Path) -> None:
        project = make_project()
        project.long_intro.intro_style = IntroStyle.PLAIN_TITLE
        track = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        assert len(track.cards) == 1
        assert track.cards[0].start_seconds == 0.0

    def test_the_scientific_name_really_fades_in(self, cards: Path) -> None:
        track = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        # DODO occupies the first four cards. The following five are distinct
        # opacity states rather than the one-frame switch in renderer v1.
        secondary_fade = track.cards[4:9]
        assert len({card.path.name for card in secondary_fade}) == 5
        assert secondary_fade[0].start_seconds == pytest.approx(1.8)

    def test_a_disabled_intro_draws_nothing_at_all(self, cards: Path) -> None:
        project = make_project()
        project.long_intro.enabled = False
        track = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        assert track.is_empty
        assert not cards.exists()

    def test_a_very_long_name_does_not_explode_into_cards(self, cards: Path) -> None:
        project = make_project()
        project.animal = Animal(
            common_name="Rodrigues Solitaire And Its Very Long Descriptive Name",
            scientific_name="Pezophaps solitaria",
        )
        track = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=cards,
        )
        assert len(track.cards) <= 44


class TestFiltergraph:
    def test_no_title_text_ever_reaches_the_graph(self, tmp_path: Path) -> None:
        """The titles are pixels in a PNG. They must not appear as a filter argument."""
        project = make_project()
        project.animal = Animal(common_name="Dodo", scientific_name="Raphus cucullatus")
        track = build_intro_track(
            project.resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=tmp_path / "intro",
        )
        steps, _ = overlay_steps(track, current="0:v", add_input=lambda _path: 1)
        graph = ";".join(steps)
        assert "Dodo" not in graph
        assert "Raphus" not in graph
        assert "EXTINCT" not in graph
        assert "drawtext" not in graph

    def test_every_card_is_gated_to_its_own_window(self, tmp_path: Path) -> None:
        track = build_intro_track(
            make_project().resolved_long_intro(),
            font_family="Inter", width=1920, height=1080, output_dir=tmp_path / "intro",
        )
        steps, final = overlay_steps(track, current="0:v", add_input=lambda _p: 1)
        assert sum(1 for step in steps if "enable='between(t," in step) == len(track.cards)
        assert final.startswith("onintro")


class TestItStaysOutOfTheShortsSource:
    def _timeline(self, project: Project):  # noqa: ANN202
        project.scenes = []
        project.intro.narration = "One."
        project.outro.enabled = False
        return build_timeline(project, word_timings={}, speech_starts={})

    def test_an_export_with_an_opening_cannot_be_its_own_clean_master(self) -> None:
        project = make_project()
        project.subtitles.burn_in = False
        timeline = self._timeline(project)

        with_intro = plan_clean_master(project, timeline)
        assert with_intro.wanted is True
        assert with_intro.reuse_primary_export is False
        assert "branded intro" in with_intro.reason

        project.long_intro.enabled = False
        without = plan_clean_master(project, timeline)
        assert without.reuse_primary_export is True

    def test_the_clean_pass_renders_with_the_opening_switched_off(self) -> None:
        project = make_project()
        clean = clean_master_project(project)
        assert clean.long_intro.enabled is False
        assert clean.subtitles.burn_in is False
        assert clean.has_long_intro is False
        # And the real project is untouched.
        assert project.long_intro.enabled is True

    def test_the_clean_pass_cannot_synthesize_long_intro_sound(self, tmp_path: Path) -> None:
        clean = clean_master_project(make_project())
        assert (
            build_long_intro_sfx(
                clean.resolved_long_intro(),
                output_dir=tmp_path,
            )
            is None
        )

    def test_opting_out_of_the_clean_master_still_opts_out(self) -> None:
        project = make_project()
        project.export.prepare_clean_master_for_shorts = False
        assert plan_clean_master(project, self._timeline(project)).wanted is False


class TestTheTimelineIsUntouched:
    def test_turning_the_opening_on_does_not_lengthen_the_video(self) -> None:
        """The intro is an overlay. The same project must time identically."""
        project = make_project()
        project.intro.narration = "The dodo lived on one island."
        project.outro.narration = "It never came back."
        project.long_intro.enabled = False
        without = build_timeline(project, word_timings={}, speech_starts={})

        project.long_intro.enabled = True
        with_intro = build_timeline(project, word_timings={}, speech_starts={})

        assert with_intro.total_duration_seconds == without.total_duration_seconds
        assert [e.start_seconds for e in with_intro.entries] == [
            e.start_seconds for e in without.entries
        ]
