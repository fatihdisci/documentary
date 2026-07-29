"""Media discovery, drafts, assets and metadata validation.

Nothing here touches the network. What is being tested is the part of publishing
that decides *what* may be uploaded and *whether the request is legal* — the
half that has to be right before a single byte is sent to YouTube.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ValidationError
from app.publishing.models import MAX_TAGS_LENGTH, PublishMode
from app.models.project import ShortsPlan
from app.storage.repository import ProjectRepository
from app.publishing.repository import PublishingRepository
from app.publishing.service import (
    PublishingService,
    clean_tags,
    parse_media_id,
    resolve_publish_at,
    tags_length,
    validate_srt,
    validate_youtube_metadata,
)
from tests.publishing_factories import (
    JPEG_BYTES,
    PNG_BYTES,
    SRT_TEXT,
    add_long_render,
    add_short,
    future_local,
    make_project,
    past_local,
    seed_draft,
)


# --- media discovery --------------------------------------------------------


def test_long_render_is_discovered_with_its_real_file_details(settings) -> None:
    project, paths = make_project(settings)
    video, checksum = add_long_render(paths, slug=project.slug)

    media = PublishingService(settings).list_media(project.slug)

    assert len(media) == 1
    item = media[0]
    assert item.media_id == "long:render0001"
    assert item.kind.value == "long"
    assert item.filename == video.name
    assert item.size_bytes == video.stat().st_size
    assert item.fingerprint.sha256 == checksum
    assert item.width == 1920 and item.height == 1080
    assert item.recommended is True
    # An absolute path must never reach the frontend.
    assert not item.url.startswith("/Users")
    assert item.url == f"/api/projects/{project.slug}/exports/{video.name}"


def test_short_is_discovered_from_its_manifest(settings) -> None:
    project, paths = make_project(settings)
    video, checksum = add_short(paths, slug=project.slug)

    media = PublishingService(settings).list_media(project.slug)

    assert [item.media_id for item in media] == ["short:short00000001"]
    item = media[0]
    assert item.kind.value == "short"
    assert item.filename == video.name
    assert item.fingerprint.sha256 == checksum
    assert item.duration_seconds == 42.0


def test_both_kinds_are_listed_together(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    add_short(paths, slug=project.slug)

    media = PublishingService(settings).list_media(project.slug)

    assert {item.kind.value for item in media} == {"long", "short"}


def test_a_render_whose_file_is_gone_is_not_offered(settings) -> None:
    project, paths = make_project(settings)
    video, _ = add_long_render(paths, slug=project.slug)
    video.unlink()

    assert PublishingService(settings).list_media(project.slug) == []


def test_preview_quality_is_flagged_and_hidden_by_default(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug, quality="preview")
    service = PublishingService(settings)

    everything = service.list_media(project.slug)
    assert everything[0].recommended is False
    assert everything[0].note is not None

    assert service.list_media(project.slug, include_unrecommended=False) == []


def test_media_id_rejects_anything_that_is_not_a_media_id(settings) -> None:
    for value in ("../../etc/passwd", "long:../secrets", "long:a/b", "", "video:1"):
        with pytest.raises(ValidationError) as excinfo:
            parse_media_id(value)
        assert excinfo.value.code.value == "path_traversal"


def test_path_traversal_in_a_media_id_never_resolves_to_a_file(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)

    with pytest.raises(AppError):
        PublishingService(settings).media_path(project.slug, "long:../../../../etc/passwd")


# --- drafts -----------------------------------------------------------------


def test_draft_is_seeded_from_the_project_metadata(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)

    response = PublishingService(settings).get_draft(project.slug, "long:render0001")

    assert response.draft.youtube.title == project.metadata.video_title
    assert response.draft.youtube.description == project.metadata.description
    assert response.draft.youtube.tags == project.metadata.tags
    assert response.draft.common.thumbnail_text == "GONE IN 80 YEARS"
    assert response.draft.youtube.category_id == "15"
    assert response.draft.youtube.default_language == "en"
    assert response.draft.youtube.default_audio_language == "en"


def test_planned_short_seeds_every_platform_draft(settings) -> None:
    project, paths = make_project(settings)
    project.shorts_plan = ShortsPlan.model_validate(
        {
            "shorts": [
                {
                    "id": "scenes-two-three",
                    "sections": [
                        {"kind": "scene", "number": 2},
                        {"kind": "scene", "number": 3},
                    ],
                    "youtube": {
                        "title": "Why the Dodo Had No Fear",
                        "description": "The full story: FULL_VIDEO_URL",
                        "tags": ["dodo short"],
                        "pinnedComment": "The dodo was not foolish.",
                    },
                    "instagram": {
                        "caption": "A bird without fear.",
                        "hashtags": ["Dodo"],
                        "cta": "Watch the full documentary.",
                    },
                    "facebook": {"caption": "The dodo evolved without land predators."},
                    "tiktok": {"caption": "Why did the dodo have no fear?"},
                }
            ]
        }
    )
    ProjectRepository(settings).save(project)
    add_short(paths, slug=project.slug, section_numbers=[2, 3])

    response = PublishingService(settings).get_draft(project.slug, "short:short00000001")

    assert response.media.content_plan_id == "scenes-two-three"
    assert response.draft.youtube.title == "Why the Dodo Had No Fear"
    assert response.draft.youtube.description == "The full story: FULL_VIDEO_URL"
    assert response.draft.instagram.caption == (
        "A bird without fear.\n\nWatch the full documentary."
    )
    assert response.draft.instagram.hashtags == ["Dodo"]
    assert response.draft.facebook.caption == "The dodo evolved without land predators."
    assert response.draft.tiktok.caption == "Why did the dodo have no fear?"


def test_editing_a_draft_never_changes_the_project_metadata(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)

    draft = service.get_draft(project.slug, "long:render0001").draft
    draft.youtube.title = "A completely different title"
    service.save_draft(project.slug, "long:render0001", draft)

    from app.storage.repository import ProjectRepository

    reloaded = ProjectRepository(settings).load(project.slug)
    assert reloaded.metadata.video_title == project.metadata.video_title


def test_drafts_are_separate_per_media_file(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    add_short(paths, slug=project.slug)
    service = PublishingService(settings)

    long_draft = service.get_draft(project.slug, "long:render0001").draft
    long_draft.youtube.title = "Long video title"
    service.save_draft(project.slug, "long:render0001", long_draft)

    short_draft = service.get_draft(project.slug, "short:short00000001").draft
    short_draft.youtube.title = "Short video title"
    service.save_draft(project.slug, "short:short00000001", short_draft)

    assert (
        service.get_draft(project.slug, "long:render0001").draft.youtube.title
        == "Long video title"
    )
    assert (
        service.get_draft(project.slug, "short:short00000001").draft.youtube.title
        == "Short video title"
    )


def test_a_changed_source_file_is_detected(settings) -> None:
    project, paths = make_project(settings)
    video, _ = add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    draft = service.get_draft(project.slug, "long:render0001").draft
    service.save_draft(project.slug, "long:render0001", draft)

    # Re-render: same name, different bytes and a manifest to match.
    add_long_render(paths, slug=project.slug, payload=b"\x00" * 40_000)

    response = service.get_draft(project.slug, "long:render0001")
    assert response.source_changed is True
    assert response.source_changed_reason is not None
    assert video.name in response.source_changed_reason or "boyut" in response.source_changed_reason


def test_saving_the_draft_rebinds_it_to_the_file_on_disk(settings) -> None:
    """The warning must be clearable: saving is how the user adopts the new file.

    Otherwise a re-render strands the draft — the panel warns forever and the
    upload stays blocked with nothing the user can do about it.
    """
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    draft = service.get_draft(project.slug, "long:render0001").draft
    service.save_draft(project.slug, "long:render0001", draft)

    # Re-render: same name and manifest, different bytes.
    add_long_render(paths, slug=project.slug, payload=b"\x00" * 40_000)
    changed = service.get_draft(project.slug, "long:render0001")
    assert changed.source_changed is True

    saved = service.save_draft(project.slug, "long:render0001", changed.draft)

    assert saved.source_changed is False
    assert saved.source_changed_reason is None
    assert saved.draft.source_fingerprint.sha256 == changed.media.fingerprint.sha256
    assert saved.draft.source_fingerprint.size_bytes == changed.media.size_bytes
    # Re-reading agrees: the new fingerprint really was written to disk.
    assert service.get_draft(project.slug, "long:render0001").source_changed is False


def test_a_rebound_draft_no_longer_blocks_the_upload(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    service.save_draft(
        project.slug, "long:render0001", service.get_draft(project.slug, "long:render0001").draft
    )

    add_long_render(paths, slug=project.slug, payload=b"\x01" * 40_000)
    with pytest.raises(AppError) as excinfo:
        service.prepare_upload(project.slug, "long:render0001", allow_duplicate=False)
    assert excinfo.value.code.value == "publishing_source_changed"

    # The user reviews the metadata and saves; the upload is prepared normally.
    service.save_draft(
        project.slug, "long:render0001", service.get_draft(project.slug, "long:render0001").draft
    )

    media, stored, video, _warnings = service.prepare_upload(
        project.slug, "long:render0001", allow_duplicate=False
    )
    assert video.is_file()
    assert stored.source_fingerprint.sha256 == media.fingerprint.sha256


def test_an_srt_beside_a_render_is_attached_only_via_the_job_artifacts(settings) -> None:
    """A stray .srt in exports/ must not be attached to an unrelated video."""
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug, with_srt=True)

    media = PublishingService(settings).list_media(project.slug)

    # No completed render job records this artifact, so nothing is attached.
    assert media[0].caption_filename is None


# --- metadata validation ----------------------------------------------------


def test_title_length_is_enforced(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    draft = service.get_draft(project.slug, "long:render0001").draft
    draft.youtube.title = "x" * 101

    with pytest.raises(ValidationError) as excinfo:
        service.save_draft(project.slug, "long:render0001", draft)
    assert excinfo.value.code.value == "youtube_invalid_metadata"


def test_description_limit_counts_utf8_bytes_not_characters(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    draft = service.get_draft(project.slug, "long:render0001").draft

    # 2600 two-byte characters: well under 5000 *characters*, over 5000 bytes.
    draft.youtube.description = "ö" * 2600
    with pytest.raises(ValidationError) as excinfo:
        service.save_draft(project.slug, "long:render0001", draft)
    assert excinfo.value.code.value == "youtube_invalid_metadata"

    draft.youtube.description = "ö" * 2400
    service.save_draft(project.slug, "long:render0001", draft)  # 4800 bytes: fine


def test_tag_total_length_is_enforced(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    service = PublishingService(settings)
    draft = service.get_draft(project.slug, "long:render0001").draft
    draft.youtube.tags = [f"tag-number-{index:03d}" for index in range(40)]

    assert tags_length(draft.youtube.tags) > MAX_TAGS_LENGTH
    with pytest.raises(ValidationError):
        service.save_draft(project.slug, "long:render0001", draft)


def test_tags_with_spaces_cost_two_extra_characters() -> None:
    assert tags_length(["dodo"]) == 4
    assert tags_length(["extinct animals"]) == 17
    assert tags_length(["dodo", "extinct animals"]) == 4 + 17 + 1


def test_tags_are_deduplicated_and_trimmed() -> None:
    assert clean_tags([" dodo ", "Dodo", "", "  ", "extinct  animals"]) == [
        "dodo",
        "extinct animals",
    ]


def test_angle_brackets_are_refused_in_title_and_description() -> None:
    from app.publishing.models import YouTubeDraft

    with pytest.raises(ValidationError):
        validate_youtube_metadata(YouTubeDraft(title="A <script> title"))
    with pytest.raises(ValidationError):
        validate_youtube_metadata(YouTubeDraft(title="Fine", description="a > b"))


# --- scheduling -------------------------------------------------------------


def test_a_future_local_time_becomes_an_offset_aware_istanbul_instant() -> None:
    resolved = resolve_publish_at(future_local())

    assert resolved.tzinfo is not None
    assert resolved.utcoffset() is not None
    # Istanbul is UTC+3 all year; the offset comes from the tz database.
    assert resolved.utcoffset().total_seconds() == 3 * 3600
    assert "+03:00" in resolved.isoformat()


def test_a_past_schedule_is_rejected_rather_than_treated_as_now() -> None:
    with pytest.raises(ValidationError) as excinfo:
        resolve_publish_at(past_local())
    assert excinfo.value.code.value == "youtube_schedule_invalid"


def test_an_empty_or_unparseable_schedule_is_rejected() -> None:
    for value in (None, "", "yarın akşam"):
        with pytest.raises(ValidationError):
            resolve_publish_at(value)


# --- assets -----------------------------------------------------------------


def test_thumbnail_accepts_png_and_jpeg_and_stores_them_in_the_project(settings) -> None:
    project, paths = make_project(settings)
    service = PublishingService(settings)

    png_name = service.store_thumbnail(project.slug, PNG_BYTES, "cover.png")
    jpeg_name = service.store_thumbnail(project.slug, JPEG_BYTES, "cover.jpeg")

    assert png_name.endswith(".png")
    assert jpeg_name.endswith(".jpg")
    assert (paths.publishing_thumbnails / png_name).is_file()
    assert (paths.publishing_thumbnails / jpeg_name).is_file()


def test_thumbnail_type_is_decided_by_content_not_by_extension(settings) -> None:
    project, _ = make_project(settings)
    service = PublishingService(settings)

    # A GIF renamed to .png must not be accepted.
    with pytest.raises(ValidationError) as excinfo:
        service.store_thumbnail(project.slug, b"GIF89a" + b"\x00" * 100, "cover.png")
    assert excinfo.value.code.value == "publishing_asset_invalid"


def test_thumbnail_over_two_megabytes_is_refused(settings) -> None:
    project, _ = make_project(settings)
    oversized = PNG_BYTES + b"\x00" * (2 * 1_048_576)

    with pytest.raises(ValidationError) as excinfo:
        PublishingService(settings).store_thumbnail(project.slug, oversized, "cover.png")
    assert excinfo.value.code.value == "file_too_large"


def test_thumbnail_filename_from_a_traversal_attempt_is_sanitized(settings) -> None:
    project, paths = make_project(settings)

    name = PublishingService(settings).store_thumbnail(
        project.slug, PNG_BYTES, "../../../../etc/passwd.png"
    )

    assert "/" not in name and ".." not in name
    assert (paths.publishing_thumbnails / name).is_file()


def test_srt_validation_accepts_a_real_srt_and_rejects_the_rest() -> None:
    assert "-->" in validate_srt(SRT_TEXT.encode("utf-8"), "captions.srt")

    with pytest.raises(ValidationError):
        validate_srt(b"", "captions.srt")
    with pytest.raises(ValidationError):
        validate_srt(b"   \n  ", "captions.srt")
    with pytest.raises(ValidationError):
        validate_srt(b"just some prose with no timings", "captions.srt")


def test_caption_is_stored_and_resolvable(settings) -> None:
    project, paths = make_project(settings)
    service = PublishingService(settings)

    name = service.store_caption(project.slug, SRT_TEXT.encode("utf-8"), "english.srt")

    assert (paths.publishing_captions / name).is_file()
    assert service.caption_path(project.slug, name, "asset").is_file()


# --- upload preparation -----------------------------------------------------


def test_prepare_upload_refuses_a_second_upload_of_the_same_file(settings) -> None:
    from datetime import datetime, timezone

    from app.errors import ConflictError
    from app.publishing.models import PublishHistoryEntry, SourceFingerprint

    project, paths = make_project(settings)
    _, checksum = add_long_render(paths, slug=project.slug)
    seed_draft(settings, project.slug, "long:render0001")
    service = PublishingService(settings)

    PublishingRepository(paths).record_upload(
        PublishHistoryEntry(
            project_slug=project.slug,
            media_id="long:render0001",
            title="The Dodo",
            video_id="vid_existing",
            video_url="https://youtu.be/vid_existing",
            uploaded_at=datetime.now(timezone.utc),
            source=SourceFingerprint(filename="the-dodo_v01.mp4", sha256=checksum),
        )
    )

    with pytest.raises(ConflictError) as excinfo:
        service.prepare_upload(project.slug, "long:render0001", allow_duplicate=False)
    assert excinfo.value.code.value == "publishing_duplicate"

    # The explicit override is the only way past it.
    media, draft, video, _ = service.prepare_upload(
        project.slug, "long:render0001", allow_duplicate=True
    )
    assert video.is_file() and media.media_id == "long:render0001"
    assert draft.youtube.title


def test_prepare_upload_rejects_a_past_schedule(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    seed_draft(
        settings,
        project.slug,
        "long:render0001",
        publish_mode=PublishMode.SCHEDULE,
        publish_at_local=past_local(),
    )

    with pytest.raises(ValidationError) as excinfo:
        PublishingService(settings).prepare_upload(
            project.slug, "long:render0001", allow_duplicate=True
        )
    assert excinfo.value.code.value == "youtube_schedule_invalid"


def test_prepare_upload_refuses_captions_without_a_file(settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    seed_draft(settings, project.slug, "long:render0001", upload_captions=True, caption_file=None)

    with pytest.raises(ValidationError) as excinfo:
        PublishingService(settings).prepare_upload(
            project.slug, "long:render0001", allow_duplicate=True
        )
    assert excinfo.value.code.value == "publishing_asset_invalid"
