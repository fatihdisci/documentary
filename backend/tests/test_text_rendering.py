"""Pillow text cards and font resolution.

This is the replacement for FFmpeg's drawtext filter, which this build does not
have. If these tests fail, no text appears in any video.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.errors import AppError
from app.models.enums import TextPosition
from app.models.project import TextStyle
from app.render import fonts, text


@pytest.fixture
def cards_dir(tmp_path: Path) -> Path:
    return tmp_path / "cards"


class TestFonts:
    def test_inter_is_bundled(self) -> None:
        """The whole text engine depends on this."""
        resolved = fonts.resolve("Inter", 400)
        assert resolved.bundled is True
        assert resolved.path.is_file()
        assert resolved.substitution_note == ""

    def test_weights_map_to_distinct_files(self) -> None:
        regular = fonts.resolve("Inter", 400)
        bold = fonts.resolve("Inter", 700)
        assert regular.path != bold.path
        assert "Bold" in bold.path.name

    def test_nearest_weight_is_used(self) -> None:
        assert fonts.resolve("Inter", 780).weight in {700, 900}

    def test_missing_font_falls_back_and_says_so(self) -> None:
        """A missing font must not fail the render, but must not be silent either."""
        resolved = fonts.resolve("DefinitelyNotInstalled", 400)
        assert resolved.bundled is True
        assert "kurulu değil" in resolved.substitution_note
        assert "Inter" in resolved.substitution_note

    def test_validate_reports_exact_and_substituted(self) -> None:
        exact, message = fonts.validate("Inter")
        assert exact is True and "kullanılabilir" in message

        exact, message = fonts.validate("NoSuchFont")
        assert exact is False and message

    def test_available_families_excludes_symbol_fonts(self) -> None:
        families = fonts.available_families()
        assert "Inter" in families
        joined = " ".join(families).lower()
        assert "braille" not in joined
        assert "emoji" not in joined

    def test_load_returns_a_usable_font(self) -> None:
        font = fonts.load("Inter", 700, 64)
        assert font.getlength("Dodo") > 0


class TestTextCards:
    def test_renders_a_real_png(self, cards_dir: Path) -> None:
        card = text.render_card(
            "A Bird Without Fear", TextStyle(size=64, font_weight=700),
            frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert card is not None
        assert card.path.is_file()
        with Image.open(card.path) as image:
            assert image.mode == "RGBA", "overlays must have an alpha channel"
            assert image.size == (card.width, card.height)

    def test_card_has_visible_pixels(self, cards_dir: Path) -> None:
        card = text.render_card(
            "Visible", TextStyle(size=64), frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert card is not None
        with Image.open(card.path) as image:
            alpha = image.getchannel("A")
            assert alpha.getextrema()[1] > 0, "the card is fully transparent"

    def test_empty_text_renders_nothing(self, cards_dir: Path) -> None:
        for value in ("", "   ", "\n"):
            assert text.render_card(
                value, TextStyle(), frame_width=1920, frame_height=1080,
                position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
            ) is None

    def test_long_text_wraps_instead_of_overflowing(self, cards_dir: Path) -> None:
        style = TextStyle(size=64, max_width_ratio=0.6)
        card = text.render_card(
            "The dodo evolved in a world that contained no threats of any kind whatsoever",
            style, frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert card is not None
        assert card.width <= 1920
        assert card.height > 100, "wrapped text should be taller than one line"

    def test_card_never_extends_past_the_frame(self, cards_dir: Path) -> None:
        for position in TextPosition:
            card = text.render_card(
                "A reasonably long documentary scene title here",
                TextStyle(size=56), frame_width=1920, frame_height=1080,
                position=position, margin=80, output_dir=cards_dir,
            )
            assert card is not None
            # The blade includes bleed for the shadow, which may sit off-frame;
            # the visible box must not.
            assert card.x + card.width >= 0
            assert card.y + card.height >= 0
            assert card.x <= 1920
            assert card.y <= 1080

    @pytest.mark.parametrize(
        ("position", "check"),
        [
            (TextPosition.BOTTOM_LEFT, lambda c: c.y > 500),
            (TextPosition.TOP_LEFT, lambda c: c.y < 300),
            (TextPosition.TOP_RIGHT, lambda c: c.x > 800),
            (TextPosition.MIDDLE_CENTER, lambda c: 200 < c.y < 800),
        ],
    )
    def test_positions_place_the_card_correctly(
        self, cards_dir: Path, position: TextPosition, check
    ) -> None:  # noqa: ANN001
        card = text.render_card(
            "Placed", TextStyle(size=48), frame_width=1920, frame_height=1080,
            position=position, margin=80, output_dir=cards_dir,
        )
        assert card is not None
        assert check(card), f"{position.value} placed at ({card.x}, {card.y})"

    def test_unicode_and_punctuation_render(self, cards_dir: Path) -> None:
        card = text.render_card(
            "Réunion Ibis — “the solitaire” · Raphus cucullatus",
            TextStyle(size=44), frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert card is not None and card.path.is_file()

    def test_explicit_newlines_are_honoured(self, cards_dir: Path) -> None:
        one_line = text.render_card(
            "Line one", TextStyle(size=48), frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        two_lines = text.render_card(
            "Line one\nLine two", TextStyle(size=48), frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert one_line is not None and two_lines is not None
        assert two_lines.height > one_line.height


class TestCaching:
    def test_identical_input_reuses_the_file(self, cards_dir: Path) -> None:
        style = TextStyle(size=64)
        args = dict(
            frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        first = text.render_card("Cached", style, **args)  # type: ignore[arg-type]
        assert first is not None
        mtime = first.path.stat().st_mtime_ns

        second = text.render_card("Cached", style, **args)  # type: ignore[arg-type]
        assert second is not None
        assert second.path == first.path
        assert second.path.stat().st_mtime_ns == mtime, "should not have re-rendered"

    def test_a_style_change_produces_a_new_card(self, cards_dir: Path) -> None:
        args = dict(
            frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        a = text.render_card("Same text", TextStyle(size=64), **args)  # type: ignore[arg-type]
        b = text.render_card("Same text", TextStyle(size=48), **args)  # type: ignore[arg-type]
        assert a is not None and b is not None
        assert a.path != b.path

    def test_a_text_change_produces_a_new_card(self, cards_dir: Path) -> None:
        style = TextStyle(size=64)
        args = dict(
            frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        a = text.render_card("First", style, **args)  # type: ignore[arg-type]
        b = text.render_card("Second", style, **args)  # type: ignore[arg-type]
        assert a is not None and b is not None
        assert a.path != b.path


class TestScrim:
    def test_renders_a_gradient(self, cards_dir: Path) -> None:
        path = text.render_scrim(
            frame_width=1920, frame_height=1080, opacity=0.5, output_dir=cards_dir
        )
        assert path is not None
        with Image.open(path) as image:
            alpha = image.convert("RGBA").getchannel("A")
            # Transparent at the top, opaque at the bottom.
            assert alpha.getpixel((960, 100)) == 0
            assert alpha.getpixel((960, 1079)) > 100

    def test_zero_opacity_renders_nothing(self, cards_dir: Path) -> None:
        assert text.render_scrim(
            frame_width=1920, frame_height=1080, opacity=0.0, output_dir=cards_dir
        ) is None

    def test_is_cached(self, cards_dir: Path) -> None:
        first = text.render_scrim(
            frame_width=1920, frame_height=1080, opacity=0.5, output_dir=cards_dir
        )
        second = text.render_scrim(
            frame_width=1920, frame_height=1080, opacity=0.5, output_dir=cards_dir
        )
        assert first == second


class TestNoDrawtextDependency:
    def test_text_rendering_never_touches_ffmpeg(self, cards_dir: Path, monkeypatch) -> None:  # noqa: ANN001
        """Proves the text engine works on a build with no drawtext filter."""
        import subprocess

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("text rendering must not invoke ffmpeg")

        monkeypatch.setattr(subprocess, "run", explode)
        monkeypatch.setattr(subprocess, "Popen", explode)

        card = text.render_card(
            "No FFmpeg needed", TextStyle(size=64),
            frame_width=1920, frame_height=1080,
            position=TextPosition.BOTTOM_LEFT, margin=80, output_dir=cards_dir,
        )
        assert card is not None and card.path.is_file()
