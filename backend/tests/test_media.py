"""Image validation, storage and thumbnails."""

from __future__ import annotations

import pytest
from PIL import Image

from app.errors import AppError, ErrorCode
from app.storage import media
from app.storage.repository import ProjectRepository
from tests.factories import make_image_bytes


@pytest.fixture
def paths(settings):  # noqa: ANN001, ANN201
    repository = ProjectRepository(settings)
    project = repository.create("Dodo")
    return repository.paths_for(project.slug)


class TestValidation:
    @pytest.mark.parametrize("fmt,name", [("PNG", "a.png"), ("JPEG", "a.jpg"), ("WEBP", "a.webp")])
    def test_accepts_supported_formats(self, fmt: str, name: str) -> None:
        image, detected = media.validate_image_bytes(make_image_bytes(fmt=fmt), name)
        assert image.width == 1920
        assert detected == fmt

    def test_rejects_unsupported_extension(self) -> None:
        with pytest.raises(AppError) as exc_info:
            media.validate_image_bytes(make_image_bytes(), "photo.gif")
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_IMAGE
        assert "PNG" in exc_info.value.details or "png" in exc_info.value.details

    def test_rejects_corrupt_data(self) -> None:
        with pytest.raises(AppError) as exc_info:
            media.validate_image_bytes(b"this is not a png", "broken.png")
        assert exc_info.value.code is ErrorCode.CORRUPT_IMAGE

    def test_rejects_truncated_file(self) -> None:
        data = make_image_bytes()
        with pytest.raises(AppError) as exc_info:
            media.validate_image_bytes(data[: len(data) // 3], "truncated.png")
        assert exc_info.value.code is ErrorCode.CORRUPT_IMAGE

    def test_rejects_images_too_small_to_render_well(self) -> None:
        with pytest.raises(AppError) as exc_info:
            media.validate_image_bytes(make_image_bytes(640, 360), "small.png")
        error = exc_info.value
        assert error.code is ErrorCode.IMAGE_TOO_SMALL
        assert "640x360" in error.message
        assert "1280x720" in (error.details or "") + error.suggestion


class TestStorage:
    def test_stores_the_original_bytes_unmodified(self, paths) -> None:  # noqa: ANN001
        data = make_image_bytes()
        stored = media.store_image(paths, data, "01-opening.png", slug="dodo")
        assert stored.path.read_bytes() == data, "the user's original must not be re-encoded"

    def test_sanitizes_the_filename(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(), "../../evil name (1).PNG", slug="dodo")
        assert stored.info.filename == "evil-name-1.png"
        assert stored.path.parent == paths.images

    def test_duplicate_names_do_not_clobber(self, paths) -> None:  # noqa: ANN001
        first = media.store_image(paths, make_image_bytes(seed=1), "scene.png", slug="dodo")
        second = media.store_image(paths, make_image_bytes(seed=2), "scene.png", slug="dodo")
        assert first.info.filename == "scene.png"
        assert second.info.filename == "scene-2.png"
        assert first.path.read_bytes() != second.path.read_bytes()

    def test_writes_a_thumbnail(self, paths) -> None:  # noqa: ANN001
        media.store_image(paths, make_image_bytes(), "01-opening.png", slug="dodo")
        thumb = paths.thumbnails / "01-opening.jpg"
        assert thumb.is_file()
        with Image.open(thumb) as image:
            assert image.width == media.THUMBNAIL_WIDTH

    def test_thumbnail_can_be_rebuilt_after_cleaning(self, paths) -> None:  # noqa: ANN001
        media.store_image(paths, make_image_bytes(), "01-opening.png", slug="dodo")
        (paths.thumbnails / "01-opening.jpg").unlink()
        rebuilt = media.rebuild_thumbnail(paths, "01-opening.png")
        assert rebuilt.is_file()

    def test_delete_removes_image_and_thumbnail(self, paths) -> None:  # noqa: ANN001
        media.store_image(paths, make_image_bytes(), "01-opening.png", slug="dodo")
        media.delete_image(paths, "01-opening.png")
        assert not (paths.images / "01-opening.png").exists()
        assert not (paths.thumbnails / "01-opening.jpg").exists()

    def test_delete_rejects_traversal(self, paths) -> None:  # noqa: ANN001
        with pytest.raises(AppError) as exc_info:
            media.delete_image(paths, "../../../project.json")
        assert exc_info.value.code is ErrorCode.PATH_TRAVERSAL


class TestWarnings:
    def test_portrait_image_warns_about_cropping(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(1440, 1920), "tall.png", slug="dodo")
        assert any("dikey" in w for w in stored.info.warnings)

    def test_square_image_warns(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(1440, 1440), "square.png", slug="dodo")
        assert any("kare" in w for w in stored.info.warnings)

    def test_ultra_wide_image_warns(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(3840, 1080), "wide.png", slug="dodo")
        assert any("çok geniş" in w for w in stored.info.warnings)

    def test_sub_1080p_warns_about_softness(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(1600, 900), "small.png", slug="dodo")
        assert any("bulanıklaşacak" in w for w in stored.info.warnings)

    def test_clean_16x9_image_has_no_warnings(self, paths) -> None:  # noqa: ANN001
        stored = media.store_image(paths, make_image_bytes(2560, 1440), "good.png", slug="dodo")
        assert stored.info.warnings == []
        assert stored.info.aspect_ratio == pytest.approx(1.7778, abs=0.001)


class TestListing:
    def test_lists_only_images(self, paths) -> None:  # noqa: ANN001
        media.store_image(paths, make_image_bytes(), "01.png", slug="dodo")
        (paths.images / "notes.txt").write_text("not an image")
        listed = media.list_images(paths, slug="dodo")
        assert [i.filename for i in listed] == ["01.png"]

    def test_unreadable_file_is_skipped_not_fatal(self, paths) -> None:  # noqa: ANN001
        media.store_image(paths, make_image_bytes(), "01.png", slug="dodo")
        (paths.images / "02-broken.png").write_bytes(b"garbage")
        listed = media.list_images(paths, slug="dodo")
        assert [i.filename for i in listed] == ["01.png"]


class TestAudio:
    def test_accepts_supported_audio(self) -> None:
        assert media.validate_audio_name("Narration Take 1.WAV") == "narration-take-1.wav"

    def test_rejects_unsupported_audio(self) -> None:
        with pytest.raises(AppError) as exc_info:
            media.validate_audio_name("voice.aiff")
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_AUDIO

    def test_imported_audio_lands_in_the_user_content_tree(self, paths) -> None:  # noqa: ANN001
        from tests.factories import make_wav_bytes

        stored = media.store_imported_audio(paths, make_wav_bytes(0.5), "take1.wav")
        assert stored.parent == paths.imported_audio
        assert paths.is_user_content(stored), "imported audio must be protected user content"
