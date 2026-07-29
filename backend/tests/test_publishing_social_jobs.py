"""Publishing to Instagram, Facebook and TikTok.

The YouTube suite already proves the queue's general behaviour. What is under
test here is what the three new platforms add, and it is mostly about
*independence*:

* each platform has its own job, its own history entry and its own duplicate
  guard, so a file already on Instagram can still go to Facebook;
* a failure on one platform never causes an upload anywhere else, and never
  makes the other platform's job fail;
* Meta is handed a temporary URL, never a path, and the temporary copy is
  removed afterwards — including when the publish failed;
* TikTok sends the file directly and refuses a privacy level the account cannot
  actually use, instead of reporting a success it did not get.

Nothing here contacts Meta, TikTok or any bucket.
"""

from __future__ import annotations

import pytest

from app.errors import AppError, ConflictError, ErrorCode
from app.models.enums import JobStatus
from app.publishing.jobs import PublishJobManager
from app.publishing.models import (
    PublishingPlatform,
    PublishJob,
    PublishRequest,
)
from app.publishing.repository import PublishingRepository
from app.publishing.service import PublishingService
from tests.publishing_factories import (
    FakeMediaHost,
    FakeMetaClient,
    FakeTikTokClient,
    FakeYouTubeClient,
    add_long_render,
    install_fake_meta,
    install_fake_tiktok,
    install_fake_youtube,
    make_project,
    meta_target,
    seed_draft,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def manager(settings) -> PublishJobManager:  # noqa: ANN001
    return PublishJobManager(settings)


@pytest.fixture
def project(settings):  # noqa: ANN001, ANN201
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    return project.slug, paths


async def _run(
    manager: PublishJobManager,
    slug: str,
    platform: PublishingPlatform,
    *,
    media_id: str = "long:render0001",
    allow_duplicate: bool = False,
) -> PublishJob:
    job = await manager.submit(
        slug, platform, PublishRequest(media_id=media_id, allow_duplicate=allow_duplicate)
    )
    await _drain(manager, job.id)
    return manager.get(job.id)


async def _drain(manager: PublishJobManager, job_id: str, *, timeout: float = 10.0) -> None:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while not manager.get(job_id).is_terminal:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job {job_id} did not finish: {manager.get(job_id).message}")
        await asyncio.sleep(0.02)


def history(settings, slug: str, platform: str) -> list:  # noqa: ANN001, ANN201
    return [
        entry
        for entry in PublishingRepository(
            PublishingService(settings).paths_for(slug)
        ).load_history()
        if entry.platform.value == platform
    ]


# --- Instagram --------------------------------------------------------------


class TestInstagram:
    async def test_a_reel_is_hosted_ingested_published_and_cleaned_up(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.COMPLETED
        assert job.video_id == "ig_media_0001"
        assert job.video_url.startswith("https://www.instagram.com/reel/")
        # Meta is handed a URL, never a path.
        assert len(client.container_calls) == 1
        assert client.container_calls[0]["videoUrl"].startswith("https://")
        # And the temporary copy does not survive the job.
        assert host.put_calls == [host.put_calls[0]]
        assert host.deleted == ["evb-test/the-dodo_v01.mp4"]
        assert job.hosted_object_key is None

    async def test_the_caption_carries_the_hashtags(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        service = PublishingService(settings)
        draft = seed_draft(settings, slug, "long:render0001")
        draft.instagram.caption = "The last dodo."
        draft.instagram.hashtags = ["dodo", "extinct animals"]
        draft.instagram.share_to_feed = False
        PublishingRepository(service.paths_for(slug)).save_draft(draft)
        client, _host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        sent = client.container_calls[0]
        assert sent["caption"] == "The last dodo.\n\n#dodo #extinctanimals"
        assert sent["shareToFeed"] is False

    async def test_the_job_waits_for_meta_to_finish_processing(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, _host = install_fake_meta(
            monkeypatch,
            FakeMetaClient(container_states=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]),
            FakeMediaHost(),
        )

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.COMPLETED
        # Publishing happened once, and only after the container was ready.
        assert client.publish_calls == ["ig_container_0001"]

    async def test_a_rejected_video_fails_without_publishing_anything(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, host = install_fake_meta(
            monkeypatch, FakeMetaClient(container_states=["ERROR"]), FakeMediaHost()
        )

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "meta_media_rejected"
        assert client.publish_calls == []
        assert job.video_id is None
        # A failed publish must not leave the video sitting on the internet.
        assert host.deleted != []
        assert history(settings, slug, "instagram") == []

    async def test_a_missing_bucket_is_reported_before_anything_is_sent(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        import app.publishing.jobs as jobs_module

        client = FakeMetaClient()
        monkeypatch.setattr(
            jobs_module.MetaCredentials, "target", lambda self: meta_target()
        )
        monkeypatch.setattr(
            jobs_module.PublishJobManager, "_meta_client", lambda self, _t: client
        )

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "media_host_not_configured"
        assert client.container_calls == []

    async def test_an_account_note_that_disagrees_becomes_a_warning(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        service = PublishingService(settings)
        draft = seed_draft(settings, slug, "long:render0001")
        draft.instagram.account = "@someone_else"
        PublishingRepository(service.paths_for(slug)).save_draft(draft)
        install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.COMPLETED
        # The typed name authorizes nothing, but a mismatch is worth saying.
        assert any("someone_else" in warning for warning in job.warnings)

    async def test_a_page_without_instagram_refuses_before_hosting(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, host = install_fake_meta(
            monkeypatch,
            FakeMetaClient(),
            FakeMediaHost(),
            target=meta_target(instagram_id=None, instagram_username=None),
        )

        job = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "meta_instagram_not_linked"
        assert host.put_calls == []


# --- Facebook ---------------------------------------------------------------


class TestFacebook:
    async def test_a_page_reel_is_started_uploaded_and_finished(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        job = await _run(manager, slug, PublishingPlatform.FACEBOOK)

        assert job.status is JobStatus.COMPLETED
        assert job.video_id == "fb_video_0001"
        assert client.fb_start_calls == 1
        assert len(client.fb_finish_calls) == 1
        # The same hosted link Instagram would use; a path is never sent.
        assert client.fb_upload_calls[0].startswith("https://")
        assert host.deleted != []

    async def test_facebook_never_touches_the_instagram_calls(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, _host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        await _run(manager, slug, PublishingPlatform.FACEBOOK)

        assert client.container_calls == []
        assert client.publish_calls == []


# --- independence between platforms ----------------------------------------


class TestPlatformIndependence:
    async def test_instagram_succeeding_does_not_block_facebook(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        instagram = await _run(manager, slug, PublishingPlatform.INSTAGRAM)
        facebook = await _run(manager, slug, PublishingPlatform.FACEBOOK)

        assert instagram.status is JobStatus.COMPLETED
        assert facebook.status is JobStatus.COMPLETED
        assert len(history(settings, slug, "instagram")) == 1
        assert len(history(settings, slug, "facebook")) == 1

    async def test_facebook_failing_leaves_the_instagram_post_alone(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        # A realistic failure at the last Facebook step, after Instagram is done.
        client = FakeMetaClient(
            finish_error=AppError(
                ErrorCode.META_API_FAILED, "Facebook gönderiyi reddetti.", http_status=502
            )
        )
        install_fake_meta(monkeypatch, client, FakeMediaHost())

        instagram = await _run(manager, slug, PublishingPlatform.INSTAGRAM)
        facebook = await _run(manager, slug, PublishingPlatform.FACEBOOK)

        assert instagram.status is JobStatus.COMPLETED
        assert facebook.status is JobStatus.FAILED
        # The Instagram post is still recorded exactly once, and nothing about
        # the Facebook failure re-sent it.
        assert len(history(settings, slug, "instagram")) == 1
        assert len(client.publish_calls) == 1

    async def test_a_file_already_on_youtube_can_still_go_to_instagram(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001", title="The Dodo")
        install_fake_youtube(monkeypatch, FakeYouTubeClient())
        install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        youtube = await manager.submit_youtube(
            slug, PublishRequest(media_id="long:render0001")
        )
        await _drain(manager, youtube.id)
        instagram = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        assert manager.get(youtube.id).status is JobStatus.COMPLETED
        # The YouTube history entry must not look like an Instagram duplicate.
        assert instagram.status is JobStatus.COMPLETED

    async def test_each_platform_keeps_its_own_duplicate_guard(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        first = await _run(manager, slug, PublishingPlatform.INSTAGRAM)
        assert first.status is JobStatus.COMPLETED

        # The same bytes to the same platform: refused.
        with pytest.raises(ConflictError) as excinfo:
            await manager.submit(
                slug, PublishingPlatform.INSTAGRAM, PublishRequest(media_id="long:render0001")
            )
        assert excinfo.value.code.value == "publishing_duplicate"

        # The same bytes to a *different* platform: allowed.
        facebook = await _run(manager, slug, PublishingPlatform.FACEBOOK)
        assert facebook.status is JobStatus.COMPLETED

    async def test_a_second_media_id_with_the_same_bytes_is_refused_per_platform(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, paths = project
        add_long_render(
            paths, slug=slug, filename="the-dodo_v02.mp4", render_job_id="render0002"
        )
        seed_draft(settings, slug, "long:render0001")
        seed_draft(settings, slug, "long:render0002")
        client, _host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())

        await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        with pytest.raises(ConflictError):
            await manager.submit(
                slug, PublishingPlatform.INSTAGRAM, PublishRequest(media_id="long:render0002")
            )
        assert len(client.publish_calls) == 1

        # An explicit override is still honoured.
        override = await _run(
            manager,
            slug,
            PublishingPlatform.INSTAGRAM,
            media_id="long:render0002",
            allow_duplicate=True,
        )
        assert override.status is JobStatus.COMPLETED
        assert len(client.publish_calls) == 2

    async def test_retrying_a_published_reel_never_publishes_it_again(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client, host = install_fake_meta(monkeypatch, FakeMetaClient(), FakeMediaHost())
        original = await _run(manager, slug, PublishingPlatform.INSTAGRAM)

        resumed = await manager.retry(original.id)
        await _drain(manager, resumed.id)

        assert manager.get(resumed.id).status is JobStatus.COMPLETED
        # One container, one publish, one hosted copy — for two job runs.
        assert len(client.container_calls) == 1
        assert len(client.publish_calls) == 1
        assert len(host.put_calls) == 1


# --- TikTok -----------------------------------------------------------------


class TestTikTok:
    async def test_a_direct_post_sends_the_file_and_waits_for_completion(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client = install_fake_tiktok(
            monkeypatch,
            FakeTikTokClient(statuses=["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"]),
        )

        job = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert job.status is JobStatus.COMPLETED
        assert len(client.upload_calls) == 1
        assert job.container_id == "tt_publish_0001"
        # No hosting layer is involved at all.
        assert job.hosted_object_key is None

    async def test_an_unaudited_app_says_the_post_is_private(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        install_fake_tiktok(monkeypatch, FakeTikTokClient(privacy_options=["SELF_ONLY"]))

        job = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert job.status is JobStatus.COMPLETED
        assert job.actual_privacy_status == "SELF_ONLY"
        assert any("yalnızca sizin" in warning for warning in job.warnings)
        # A self-only post has no public URL, and none is invented.
        assert job.video_url == ""

    async def test_a_privacy_the_account_cannot_use_is_refused(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        service = PublishingService(settings)
        draft = seed_draft(settings, slug, "long:render0001")
        draft.tiktok.privacy = "PUBLIC_TO_EVERYONE"
        PublishingRepository(service.paths_for(slug)).save_draft(draft)
        client = install_fake_tiktok(
            monkeypatch, FakeTikTokClient(privacy_options=["SELF_ONLY"])
        )

        job = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "tiktok_privacy_not_allowed"
        # Refused before a single byte was sent.
        assert client.upload_calls == []

    async def test_a_failed_post_is_reported_as_failed_not_as_success(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        install_fake_tiktok(
            monkeypatch,
            FakeTikTokClient(statuses=["FAILED"], fail_reason="picture_size_check_failed"),
        )

        job = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "tiktok_upload_failed"
        assert "picture_size_check_failed" in (job.error_details or "")
        assert history(settings, slug, "tiktok") == []

    async def test_a_public_post_gets_a_real_link(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        service = PublishingService(settings)
        draft = seed_draft(settings, slug, "long:render0001")
        draft.tiktok.privacy = "PUBLIC_TO_EVERYONE"
        PublishingRepository(service.paths_for(slug)).save_draft(draft)
        install_fake_tiktok(
            monkeypatch,
            FakeTikTokClient(
                privacy_options=["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                post_ids=["7300000000000000000"],
            ),
        )

        job = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert job.status is JobStatus.COMPLETED
        assert job.video_id == "7300000000000000000"
        assert "tiktok.com/@vanishedearthdocs/video/" in job.video_url

    async def test_tiktok_has_its_own_duplicate_guard(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client = install_fake_tiktok(monkeypatch, FakeTikTokClient())

        await _run(manager, slug, PublishingPlatform.TIKTOK)

        with pytest.raises(ConflictError):
            await manager.submit(
                slug, PublishingPlatform.TIKTOK, PublishRequest(media_id="long:render0001")
            )
        assert len(client.upload_calls) == 1


# --- the HTTP surface -------------------------------------------------------


class TestEndpoints:
    def test_publishing_without_a_connection_is_a_clear_refusal(
        self, client, settings
    ) -> None:
        project, paths = make_project(settings)
        add_long_render(paths, slug=project.slug)
        seed_draft(settings, project.slug, "long:render0001")

        response = client.post(
            f"/api/projects/{project.slug}/publishing/instagram",
            json={"mediaId": "long:render0001", "allowDuplicate": False},
        )

        # Queued, then failed with an actionable error — never a fake success.
        assert response.status_code == 202
        job_id = response.json()["id"]
        for _ in range(100):
            job = client.get(f"/api/publishing/jobs/{job_id}").json()
            if job["status"] in {"failed", "completed", "cancelled", "interrupted"}:
                break
        assert job["status"] == "failed"
        assert job["errorCode"] in {"meta_auth_required", "media_host_not_configured"}
        assert job["errorSuggestion"]
