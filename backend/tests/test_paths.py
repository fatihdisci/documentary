"""Path sanitization and traversal prevention.

These are security tests: a failure here means user-supplied text can reach a
place on disk it should not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import AppError, ErrorCode
from app.storage.paths import (
    natural_sort_key,
    safe_join,
    sanitize_filename,
    slugify,
    unique_path,
)


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Dodo", "dodo"),
            ("Raphus cucullatus", "raphus-cucullatus"),
            ("Réunion Ibis", "reunion-ibis"),  # accents folded, not dropped
            ("  spaced   out  ", "spaced-out"),
            ("../../etc/passwd", "etc-passwd"),
            ("a//b\\c", "a-b-c"),
            ("Thylacine (Tasmanian Tiger)", "thylacine-tasmanian-tiger"),
            ("...", "untitled"),
            ("", "untitled"),
            ("!!!@@@###", "untitled"),
            ("CON", "untitled"),  # Windows reserved device name
        ],
    )
    def test_produces_safe_component(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_never_contains_separators(self) -> None:
        for raw in ["a/b", "a\\b", "../..", "/absolute/path", "x\x00y"]:
            result = slugify(raw)
            assert "/" not in result
            assert "\\" not in result
            assert "\x00" not in result

    def test_length_is_bounded(self) -> None:
        assert len(slugify("x" * 500)) <= 96


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("01-opening.png", "01-opening.png"),
            ("photo (1).PNG", "photo-1.png"),
            ("../../../etc/passwd.png", "passwd.png"),
            ("..\\..\\windows\\system32\\evil.jpg", "evil.jpg"),
            ("scène-två.webp", "scene-tva.webp"),
        ],
    )
    def test_keeps_extension_drops_traversal(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_extensionless_input(self) -> None:
        assert sanitize_filename("README") == "readme"

    def test_implausible_extension_is_folded_into_the_stem(self) -> None:
        """A 20-character 'extension' is not one, so it is kept as part of the name.

        The point is that nothing is silently lost and the result is still safe —
        not that the text is stripped.
        """
        result = sanitize_filename("file.thisisnotanextension")
        assert "/" not in result and "\\" not in result
        assert result.startswith("file")

    def test_traversal_wins_over_extension_preservation(self) -> None:
        # Even with a plausible extension, only the final component survives.
        assert "/" not in sanitize_filename("../../a/b/c.png")


class TestSafeJoin:
    def test_allows_paths_inside_root(self, tmp_path: Path) -> None:
        assert safe_join(tmp_path, "images", "a.png") == (tmp_path / "images" / "a.png").resolve()

    def test_allows_the_root_itself(self, tmp_path: Path) -> None:
        assert safe_join(tmp_path) == tmp_path.resolve()

    @pytest.mark.parametrize(
        "attack",
        [
            "../outside.png",
            "../../etc/passwd",
            "images/../../escape.txt",
            "/etc/passwd",
            "images/../../../../../../tmp/evil",
        ],
    )
    def test_rejects_traversal(self, tmp_path: Path, attack: str) -> None:
        root = tmp_path / "project"
        root.mkdir()
        with pytest.raises(AppError) as exc_info:
            safe_join(root, attack)
        assert exc_info.value.code is ErrorCode.PATH_TRAVERSAL

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        (root / "images").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "images" / "link").symlink_to(outside)
        with pytest.raises(AppError) as exc_info:
            safe_join(root, "images", "link", "secret.txt")
        assert exc_info.value.code is ErrorCode.PATH_TRAVERSAL


class TestUniquePath:
    def test_versions_instead_of_overwriting(self, tmp_path: Path) -> None:
        first = unique_path(tmp_path, "dodo", ".mp4")
        assert first.name == "dodo_v01.mp4"
        first.touch()

        second = unique_path(tmp_path, "dodo", ".mp4")
        assert second.name == "dodo_v02.mp4"
        second.touch()

        assert unique_path(tmp_path, "dodo", ".mp4").name == "dodo_v03.mp4"
        # The originals are untouched.
        assert first.exists() and second.exists()


class TestNaturalSort:
    def test_orders_numerically_not_lexically(self) -> None:
        names = ["10-extinction.png", "2-habitat.png", "1-opening.png"]
        assert sorted(names, key=natural_sort_key) == [
            "1-opening.png",
            "2-habitat.png",
            "10-extinction.png",
        ]

    def test_handles_zero_padded_names(self) -> None:
        names = ["03-c.png", "01-a.png", "02-b.png"]
        assert sorted(names, key=natural_sort_key) == ["01-a.png", "02-b.png", "03-c.png"]
