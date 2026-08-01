"""Publishing to TikTok.

The YouTube suite already proves the queue's general behaviour. What is under
test here is what TikTok adds, and it is mostly about *independence*:

* each platform has its own job, its own history entry and its own duplicate
  guard, so a file already on YouTube can still go to TikTok;
* a failure on one platform never causes an upload anywhere else, and never
  makes the other platform's job fail;
* TikTok sends the file directly and refuses a privacy level the account cannot
  actually use, instead of reporting a success it did not get.

Nothing here contacts TikTok.
"""

from __future__ import annotations

import pytest

from app.errors import ConflictError
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
    FakeTikTokClient,
    FakeYouTubeClient,
    add_long_render,
    install_fake_tiktok,
    install_fake_youtube,
    make_project,
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


class TestPlatformIndependence:
    async def test_youtube_succeeding_does_not_block_tiktok(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001", title="The Dodo")
        install_fake_youtube(monkeypatch, FakeYouTubeClient())
        install_fake_tiktok(monkeypatch, FakeTikTokClient())

        youtube = await _run(manager, slug, PublishingPlatform.YOUTUBE)
        tiktok = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert youtube.status is JobStatus.COMPLETED
        assert tiktok.status is JobStatus.COMPLETED
        assert len(history(settings, slug, "youtube")) == 1
        assert len(history(settings, slug, "tiktok")) == 1

    async def test_tiktok_failing_leaves_the_youtube_upload_alone(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001", title="The Dodo")
        youtube_client = install_fake_youtube(monkeypatch, FakeYouTubeClient())
        install_fake_tiktok(
            monkeypatch,
            FakeTikTokClient(statuses=["FAILED"], fail_reason="picture_size_check_failed"),
        )

        youtube = await _run(manager, slug, PublishingPlatform.YOUTUBE)
        tiktok = await _run(manager, slug, PublishingPlatform.TIKTOK)

        assert youtube.status is JobStatus.COMPLETED
        assert tiktok.status is JobStatus.FAILED
        # The YouTube video is still recorded exactly once, and nothing about
        # the TikTok failure re-sent it.
        assert len(history(settings, slug, "youtube")) == 1
        assert len(youtube_client.upload_calls) == 1

    async def test_each_platform_keeps_its_own_duplicate_guard(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001", title="The Dodo")
        install_fake_youtube(monkeypatch, FakeYouTubeClient())
        install_fake_tiktok(monkeypatch, FakeTikTokClient())

        first = await _run(manager, slug, PublishingPlatform.TIKTOK)
        assert first.status is JobStatus.COMPLETED

        # The same bytes to the same platform: refused.
        with pytest.raises(ConflictError) as excinfo:
            await manager.submit(
                slug, PublishingPlatform.TIKTOK, PublishRequest(media_id="long:render0001")
            )
        assert excinfo.value.code.value == "publishing_duplicate"

        # The same bytes to a *different* platform: allowed.
        youtube = await _run(manager, slug, PublishingPlatform.YOUTUBE)
        assert youtube.status is JobStatus.COMPLETED

    async def test_a_second_media_id_with_the_same_bytes_is_refused_per_platform(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, paths = project
        add_long_render(
            paths, slug=slug, filename="the-dodo_v02.mp4", render_job_id="render0002"
        )
        seed_draft(settings, slug, "long:render0001")
        seed_draft(settings, slug, "long:render0002")
        client = install_fake_tiktok(monkeypatch, FakeTikTokClient())

        await _run(manager, slug, PublishingPlatform.TIKTOK)

        with pytest.raises(ConflictError):
            await manager.submit(
                slug, PublishingPlatform.TIKTOK, PublishRequest(media_id="long:render0002")
            )
        assert len(client.upload_calls) == 1

        # An explicit override is still honoured.
        override = await _run(
            manager,
            slug,
            PublishingPlatform.TIKTOK,
            media_id="long:render0002",
            allow_duplicate=True,
        )
        assert override.status is JobStatus.COMPLETED
        assert len(client.upload_calls) == 2

    async def test_retrying_a_published_post_never_publishes_it_again(
        self, manager, project, settings, monkeypatch
    ) -> None:
        slug, _paths = project
        seed_draft(settings, slug, "long:render0001")
        client = install_fake_tiktok(monkeypatch, FakeTikTokClient())
        original = await _run(manager, slug, PublishingPlatform.TIKTOK)

        resumed = await manager.retry(original.id)
        await _drain(manager, resumed.id)

        assert manager.get(resumed.id).status is JobStatus.COMPLETED
        # One publish session, one upload — for two job runs.
        assert len(client.init_calls) == 1
        assert len(client.upload_calls) == 1


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
            f"/api/projects/{project.slug}/publishing/tiktok",
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
        assert job["errorCode"] in {"tiktok_app_missing", "tiktok_auth_required"}
        assert job["errorSuggestion"]
