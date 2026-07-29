"""The upload queue: progress, scheduling, partial failures and crash recovery.

Every Google call is mocked. What is under test is the app's own behaviour — in
particular the property the whole design turns on: **a video is never uploaded
twice.** A thumbnail or caption failure leaves the video where it is and lets the
user retry only the step that failed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import AppError, ConflictError
from app.models.enums import JobStatus
from app.publishing.jobs import PublishJobManager
from app.publishing.models import (
    AssetStatus,
    PublishHistoryEntry,
    PublishJob,
    PublishMode,
    PublishRequest,
    PrivacyStatus,
    SourceFingerprint,
)
from app.publishing.repository import PublishingRepository
from app.publishing.service import PublishingService
from tests.publishing_factories import (
    PNG_BYTES,
    SRT_TEXT,
    FakeYouTubeClient,
    add_long_render,
    add_short,
    future_local,
    install_fake_youtube,
    make_project,
    seed_draft,
)


@pytest.fixture
def manager(settings) -> PublishJobManager:  # noqa: ANN001
    return PublishJobManager(settings)


@pytest.fixture
def project(settings):  # noqa: ANN001, ANN201
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    return project.slug, paths


async def _run(manager: PublishJobManager, slug: str, **kwargs) -> PublishJob:
    """Submit an upload and let the worker finish it."""
    job = await manager.submit_youtube(
        slug, PublishRequest(media_id=kwargs.pop("media_id", "long:render0001"), **kwargs)
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


# --- the job model ----------------------------------------------------------


class TestJobModel:
    def test_starts_queued_and_active(self) -> None:
        job = PublishJob(
            project_slug="x", media_id="long:1", source=SourceFingerprint(filename="a.mp4")
        )
        assert job.status is JobStatus.QUEUED
        assert job.is_active and not job.is_terminal

    @pytest.mark.parametrize(
        "status",
        [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED],
    )
    def test_terminal_states(self, status: JobStatus) -> None:
        job = PublishJob(
            project_slug="x",
            media_id="long:1",
            source=SourceFingerprint(filename="a.mp4"),
            status=status,
        )
        assert job.is_terminal and not job.is_active


# --- a successful upload ----------------------------------------------------


class TestSuccessfulUpload:
    async def test_uploads_resumably_and_records_the_video(
        self, manager, project, monkeypatch
    ) -> None:
        slug, paths = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient(chunks=4))

        job = await _run(manager, slug)

        assert job.status is JobStatus.COMPLETED
        assert job.video_id == "vid_test_0001"
        assert job.video_url == "https://youtu.be/vid_test_0001"
        assert len(client.upload_calls) == 1
        # The file that was uploaded is the export, not something reconstructed.
        assert client.upload_calls[0]["video"].name == "the-dodo_v01.mp4"

    async def test_upload_progress_is_written_to_the_job(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient(chunks=4))

        job = await _run(manager, slug)

        assert client.progress_reports  # the fake really did report progress
        assert job.total_bytes > 0
        assert job.uploaded_bytes == job.total_bytes
        assert job.progress == 1.0

    async def test_the_request_body_matches_the_draft(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(
            settings_of(manager),
            slug,
            "long:render0001",
            privacy_status=PrivacyStatus.UNLISTED,
            notify_subscribers=False,
            made_for_kids=True,
            embeddable=False,
        )
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        await _run(manager, slug)

        call = client.upload_calls[0]
        snippet = call["body"]["snippet"]
        status = call["body"]["status"]
        assert snippet["title"] == "The Dodo: A Bird That Never Learned to Run"
        assert snippet["categoryId"] == "15"
        assert snippet["defaultLanguage"] == "en"
        assert snippet["defaultAudioLanguage"] == "en"
        assert status["privacyStatus"] == "unlisted"
        assert status["selfDeclaredMadeForKids"] is True
        assert status["embeddable"] is False
        assert call["notifySubscribers"] is False
        # Not scheduled: publishAt must be absent, never a null.
        assert "publishAt" not in status

    async def test_history_is_written_with_the_video_id(
        self, manager, project, monkeypatch
    ) -> None:
        slug, paths = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        install_fake_youtube(monkeypatch, FakeYouTubeClient())

        await _run(manager, slug)

        history = PublishingRepository(paths).load_history()
        assert len(history) == 1
        assert history[0].video_id == "vid_test_0001"
        assert history[0].source.sha256  # the fingerprint the upload was bound to

    async def test_a_short_can_be_uploaded_too(self, manager, settings, monkeypatch) -> None:
        project, paths = make_project(settings, name="Shorts Publishing")
        add_short(paths, slug=project.slug)
        seed_draft(settings, project.slug, "short:short00000001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        job = await _run(manager, project.slug, media_id="short:short00000001")

        assert job.status is JobStatus.COMPLETED
        assert client.upload_calls[0]["video"].name == "the-dodo-short-abc123.mp4"


# --- scheduling -------------------------------------------------------------


class TestScheduling:
    async def test_a_scheduled_upload_is_private_with_an_rfc3339_publish_at(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(
            settings_of(manager),
            slug,
            "long:render0001",
            publish_mode=PublishMode.SCHEDULE,
            publish_at_local=future_local(),
            privacy_status=PrivacyStatus.PUBLIC,
        )
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        job = await _run(manager, slug)

        status = client.upload_calls[0]["body"]["status"]
        assert status["privacyStatus"] == "private"
        assert status["publishAt"].endswith("+03:00")
        assert datetime.fromisoformat(status["publishAt"]) > datetime.now(timezone.utc)
        assert job.requested_publish_at is not None


# --- thumbnails and captions ------------------------------------------------


class TestExtras:
    async def test_thumbnail_is_set_after_the_video_uploads(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        service = PublishingService(settings_of(manager))
        name = service.store_thumbnail(slug, PNG_BYTES, "cover.png")
        seed_draft(settings_of(manager), slug, "long:render0001", thumbnail_file=name)
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        job = await _run(manager, slug)

        assert job.thumbnail_status is AssetStatus.UPLOADED
        assert client.thumbnail_calls == [("vid_test_0001", service.thumbnail_path(slug, name))]

    async def test_a_failed_thumbnail_does_not_fail_the_upload(
        self, manager, project, monkeypatch
    ) -> None:
        from app.errors import ErrorCode

        slug, paths = project
        name = PublishingService(settings_of(manager)).store_thumbnail(
            slug, PNG_BYTES, "cover.png"
        )
        seed_draft(settings_of(manager), slug, "long:render0001", thumbnail_file=name)
        install_fake_youtube(
            monkeypatch,
            FakeYouTubeClient(
                thumbnail_error=AppError(
                    ErrorCode.YOUTUBE_THUMBNAIL_FAILED, "Kapak görseli reddedildi."
                )
            ),
        )

        job = await _run(manager, slug)

        assert job.status is JobStatus.COMPLETED
        assert job.video_id == "vid_test_0001"
        assert job.thumbnail_status is AssetStatus.FAILED
        assert job.thumbnail_error
        assert any("Kapak" in warning for warning in job.warnings)
        # The video is still in the history, with its link.
        assert PublishingRepository(paths).load_history()[0].video_id == "vid_test_0001"

    async def test_captions_are_uploaded_when_asked_for(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        service = PublishingService(settings_of(manager))
        name = service.store_caption(slug, SRT_TEXT.encode("utf-8"), "english.srt")
        seed_draft(
            settings_of(manager),
            slug,
            "long:render0001",
            caption_file=name,
            caption_source="asset",
            upload_captions=True,
        )
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        job = await _run(manager, slug)

        assert job.caption_status is AssetStatus.UPLOADED
        assert job.caption_track_id == "caption_track_0001"
        assert len(client.caption_calls) == 1

    async def test_a_failed_caption_never_re_uploads_the_video(
        self, manager, project, monkeypatch
    ) -> None:
        from app.errors import ErrorCode

        slug, paths = project
        service = PublishingService(settings_of(manager))
        name = service.store_caption(slug, SRT_TEXT.encode("utf-8"), "english.srt")
        seed_draft(
            settings_of(manager),
            slug,
            "long:render0001",
            caption_file=name,
            caption_source="asset",
            upload_captions=True,
        )
        client = install_fake_youtube(
            monkeypatch,
            FakeYouTubeClient(
                caption_error=AppError(
                    ErrorCode.YOUTUBE_CAPTION_FAILED, "Altyazı kabul edilmedi."
                )
            ),
        )

        job = await _run(manager, slug)
        assert job.status is JobStatus.COMPLETED
        assert job.caption_status is AssetStatus.FAILED
        assert len(client.upload_calls) == 1

        # Retrying resumes: the caption is attempted again, the video is not.
        client.caption_error = None
        retried = await manager.retry(job.id)
        await _drain(manager, retried.id)
        finished = manager.get(retried.id)

        assert len(client.upload_calls) == 1, "the video must never be uploaded twice"
        assert finished.video_id == "vid_test_0001"
        assert finished.caption_status is AssetStatus.UPLOADED
        assert PublishingRepository(paths).load_history()[0].caption_status is (
            AssetStatus.UPLOADED
        )


# --- duplicate protection and source changes --------------------------------


class TestDuplicateProtection:
    async def test_the_same_file_is_refused_a_second_time(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        await _run(manager, slug)

        with pytest.raises(ConflictError) as excinfo:
            await manager.submit_youtube(
                slug, PublishRequest(media_id="long:render0001", allow_duplicate=False)
            )
        assert excinfo.value.code.value == "publishing_duplicate"
        assert len(client.upload_calls) == 1

    async def test_an_explicit_override_allows_a_second_upload(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        await _run(manager, slug)
        await _run(manager, slug, allow_duplicate=True)

        assert len(client.upload_calls) == 2

    async def test_the_same_bytes_under_a_second_media_id_are_refused(
        self, manager, project, monkeypatch
    ) -> None:
        """Two exports of the same file are one video, whatever they are called."""
        slug, paths = project
        add_long_render(
            paths, slug=slug, filename="the-dodo_v02.mp4", render_job_id="render0002"
        )
        seed_draft(settings_of(manager), slug, "long:render0001")
        seed_draft(settings_of(manager), slug, "long:render0002")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        await _run(manager, slug, media_id="long:render0001")

        with pytest.raises(ConflictError) as excinfo:
            await manager.submit_youtube(slug, PublishRequest(media_id="long:render0002"))
        assert excinfo.value.code.value == "publishing_duplicate"
        assert len(client.upload_calls) == 1

        # The user confirms they really want a second video on the channel.
        await _run(manager, slug, media_id="long:render0002", allow_duplicate=True)
        assert len(client.upload_calls) == 2

    async def test_the_same_bytes_cannot_be_queued_while_the_first_upload_runs(
        self, manager, project, monkeypatch
    ) -> None:
        """The history only knows about *finished* uploads; the queue is checked too."""
        import asyncio
        import threading

        slug, paths = project
        add_long_render(
            paths, slug=slug, filename="the-dodo_v02.mp4", render_job_id="render0002"
        )
        seed_draft(settings_of(manager), slug, "long:render0001")
        seed_draft(settings_of(manager), slug, "long:render0002")

        gate = threading.Event()

        class BlockingClient(FakeYouTubeClient):
            def upload_video(self, video, **kwargs):  # noqa: ANN001, ANN003, ANN201
                gate.wait(timeout=5)
                return super().upload_video(video, **kwargs)

        client = install_fake_youtube(monkeypatch, BlockingClient())
        first = await manager.submit_youtube(slug, PublishRequest(media_id="long:render0001"))
        try:
            for _ in range(100):
                if manager.get(first.id).status is JobStatus.RUNNING:
                    break
                await asyncio.sleep(0.02)

            with pytest.raises(ConflictError) as excinfo:
                await manager.submit_youtube(
                    slug, PublishRequest(media_id="long:render0002")
                )
            assert excinfo.value.code.value == "publishing_duplicate"
        finally:
            gate.set()
            await _drain(manager, first.id)

        assert len(client.upload_calls) == 1

    async def test_a_duplicate_that_lands_while_the_job_waits_is_refused(
        self, manager, project, monkeypatch
    ) -> None:
        """The queue is not the channel: the check runs again before the bytes go.

        The upload of the same file is simulated as landing while this job is on
        its way to the wire — hashing is where the job learns what it is about to
        send, so that is where the history is consulted for the last time.
        """
        import app.publishing.jobs as jobs_module

        slug, paths = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())
        repository = PublishingRepository(paths)
        real_hash = jobs_module.sha256_file
        landed: list[str] = []

        def racing(path):  # noqa: ANN001, ANN202
            digest = real_hash(path)
            if not landed:
                landed.append(digest)
                repository.record_upload(
                    PublishHistoryEntry(
                        project_slug=slug,
                        media_id="long:render0002",
                        filename="the-dodo_v02.mp4",
                        title="Aynı dosya, başka kimlik",
                        video_id="vid_other_0001",
                        video_url="https://youtu.be/vid_other_0001",
                        source=SourceFingerprint(
                            filename="the-dodo_v02.mp4", sha256=digest
                        ),
                    )
                )
            return digest

        monkeypatch.setattr(jobs_module, "sha256_file", racing)

        job = await _run(manager, slug)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "publishing_duplicate"
        assert client.upload_calls == [], "the video must never be uploaded twice"

    async def test_an_override_still_uploads_a_duplicate_that_landed_meanwhile(
        self, manager, project, monkeypatch
    ) -> None:
        import app.publishing.jobs as jobs_module

        slug, paths = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())
        repository = PublishingRepository(paths)
        real_hash = jobs_module.sha256_file
        landed: list[str] = []

        def racing(path):  # noqa: ANN001, ANN202
            digest = real_hash(path)
            if not landed:
                landed.append(digest)
                repository.record_upload(
                    PublishHistoryEntry(
                        project_slug=slug,
                        media_id="long:render0002",
                        filename="the-dodo_v02.mp4",
                        title="Aynı dosya, başka kimlik",
                        video_id="vid_other_0001",
                        video_url="https://youtu.be/vid_other_0001",
                        source=SourceFingerprint(
                            filename="the-dodo_v02.mp4", sha256=digest
                        ),
                    )
                )
            return digest

        monkeypatch.setattr(jobs_module, "sha256_file", racing)

        job = await _run(manager, slug, allow_duplicate=True)

        assert job.status is JobStatus.COMPLETED
        assert len(client.upload_calls) == 1

    async def test_a_source_that_changed_before_the_upload_is_refused(
        self, manager, project, monkeypatch
    ) -> None:
        slug, paths = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        client = install_fake_youtube(monkeypatch, FakeYouTubeClient())

        # Replace the bytes but leave the manifest describing the old file, so the
        # size still matches and only the checksum gives it away.
        video = paths.exports / "the-dodo_v01.mp4"
        original = video.read_bytes()
        video.write_bytes(b"\xff" * len(original))

        job = await _run(manager, slug, allow_duplicate=True)

        assert job.status is JobStatus.FAILED
        assert job.error_code == "publishing_source_changed"
        assert client.upload_calls == []


# --- crash recovery and cancellation ----------------------------------------


class TestRecovery:
    def test_a_job_left_running_by_a_dead_process_becomes_interrupted(
        self, manager, settings
    ) -> None:
        history = settings.data_dir / "publishing-jobs"
        history.mkdir(parents=True, exist_ok=True)
        job = PublishJob(
            project_slug="the-dodo",
            media_id="long:render0001",
            source=SourceFingerprint(filename="the-dodo_v01.mp4"),
            status=JobStatus.RUNNING,
            pid=999_999,  # not a live process
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        (history / f"{job.id}.json").write_text(job.model_dump_json(), "utf-8")

        manager.load_history()

        recovered = manager.get(job.id)
        assert recovered.status is JobStatus.INTERRUPTED
        assert recovered.finished_at is not None
        assert recovered.error_suggestion
        # The state is on disk, not only in memory.
        stored = json.loads((history / f"{job.id}.json").read_text("utf-8"))
        assert stored["status"] == "interrupted"

    async def test_a_queued_job_can_be_cancelled_before_it_starts(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(settings_of(manager), slug, "long:render0001")
        install_fake_youtube(monkeypatch, FakeYouTubeClient())

        job = await manager.submit_youtube(slug, PublishRequest(media_id="long:render0001"))
        if not manager.get(job.id).is_terminal:
            await manager.cancel(job.id)
        assert manager.get(job.id).status in {JobStatus.CANCELLED, JobStatus.COMPLETED}

    async def test_two_uploads_of_the_same_file_cannot_run_at_once(
        self, manager, project, monkeypatch
    ) -> None:
        slug, _ = project
        seed_draft(settings_of(manager), slug, "long:render0001")

        # A client that blocks in the first chunk, so the first job stays running.
        import threading

        gate = threading.Event()

        class BlockingClient(FakeYouTubeClient):
            def upload_video(self, video, **kwargs):  # noqa: ANN001, ANN003, ANN201
                gate.wait(timeout=5)
                return super().upload_video(video, **kwargs)

        install_fake_youtube(monkeypatch, BlockingClient())
        first = await manager.submit_youtube(slug, PublishRequest(media_id="long:render0001"))
        try:
            import asyncio

            for _ in range(50):
                if manager.get(first.id).status is JobStatus.RUNNING:
                    break
                await asyncio.sleep(0.02)

            with pytest.raises(ConflictError):
                await manager.submit_youtube(
                    slug, PublishRequest(media_id="long:render0001", allow_duplicate=True)
                )
        finally:
            gate.set()
            await _drain(manager, first.id)


def settings_of(manager: PublishJobManager):  # noqa: ANN201
    return manager.settings
