"""Fixtures for the publishing suite.

Real files on disk wherever a real file will do — a manifest, an MP4-shaped
blob, an SRT, a PNG — because the code under test checks sizes, checksums and
content signatures, and a mock would prove nothing about any of that.

Google itself is never contacted: :class:`FakeYouTubeClient` stands in for the
API wrapper, and the credential objects here are dummies with the right shape.
No test in this suite has, or needs, a real token.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.project import Project
from app.publishing.models import PublishDraft, PublishMode, SourceFingerprint
from app.publishing.repository import PublishingRepository
from app.publishing.service import PublishingService
from app.shorts.manifest import ManifestProfile, sha256_file
from app.shorts.models import ShortManifest
from app.storage.layout import ProjectPaths
from app.storage.repository import ProjectRepository
from tests.shorts_factories import make_manifest, write_manifest

#: Enough bytes that size and checksum checks are meaningful, small enough that
#: hashing one in a test is free.
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"evb-test-video-payload" * 512

SRT_TEXT = (
    "1\n00:00:01,000 --> 00:00:03,500\nA bright green parrot.\n\n"
    "2\n00:00:03,600 --> 00:00:06,000\nThe last one died in 1918.\n"
)

def _image_bytes(fmt: str) -> bytes:
    """A small but genuinely decodable image.

    Generated rather than hard-coded: the thumbnail check decodes the file with
    Pillow, so a hand-written byte string that merely *starts* with the right
    magic would test the wrong thing.
    """
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (30, 60, 90)).save(buffer, fmt)
    return buffer.getvalue()


PNG_BYTES = _image_bytes("PNG")
JPEG_BYTES = _image_bytes("JPEG")


def make_project(settings: Settings, *, name: str = "The Dodo") -> tuple[Project, ProjectPaths]:
    """A saved project with metadata worth seeding a draft from."""
    repository = ProjectRepository(settings)
    project = repository.create(name)
    project.metadata.video_title = "The Dodo: A Bird That Never Learned to Run"
    project.metadata.description = "A documentary about the dodo.\n\n#Dodo #ExtinctAnimals"
    project.metadata.tags = ["dodo", "extinct animals", "natural history"]
    project.metadata.thumbnail_text = "GONE IN 80 YEARS"
    project.metadata.thumbnail_prompt = "A dodo on a Mauritius beach at dusk"
    repository.save(project)
    return project, repository.paths_for(project.slug)


def add_long_render(
    paths: ProjectPaths,
    *,
    slug: str = "the-dodo",
    filename: str = "the-dodo_v01.mp4",
    render_job_id: str = "render0001",
    quality: str = "youtube-hq",
    payload: bytes = VIDEO_BYTES,
    with_srt: bool = False,
) -> tuple[Path, str]:
    """A finished long export plus the manifest that proves it finished."""
    paths.ensure()
    video = paths.exports / filename
    video.write_bytes(payload)

    manifest = make_manifest(video, slug=slug, render_job_id=render_job_id)
    manifest = manifest.model_copy(
        update={
            "profile": ManifestProfile(
                width=manifest.profile.width,
                height=manifest.profile.height,
                fps=manifest.profile.fps,
                quality=quality,
            )
        }
    )
    write_manifest(manifest, video)

    if with_srt:
        (paths.exports / f"{video.stem}.srt").write_text(SRT_TEXT, "utf-8")

    return video, sha256_file(video)


def add_short(
    paths: ProjectPaths,
    *,
    slug: str = "the-dodo",
    short_id: str = "short00000001",
    filename: str = "the-dodo-short-abc123.mp4",
    payload: bytes = VIDEO_BYTES + b"short",
) -> tuple[Path, str]:
    """A finished Short plus its manifest, exactly as the Shorts pipeline writes."""
    paths.ensure()
    video = paths.shorts_exports / filename
    video.write_bytes(payload)
    checksum = sha256_file(video)

    manifest = ShortManifest(
        short_id=short_id,
        project_slug=slug,
        filename=filename,
        cache_key=short_id,
        created_at=datetime.now(timezone.utc),
        job_id="shortjob0001",
        source_render_id="render0001",
        source_video="the-dodo_v01.mp4",
        source_sha256="b" * 64,
        duration_seconds=42.0,
        size_bytes=video.stat().st_size,
        sha256=checksum,
        request={"sourceRenderId": "render0001", "segments": []},
    )
    (paths.shorts_exports / f"{video.stem}.json").write_text(
        manifest.model_dump_json(indent=2), "utf-8"
    )
    return video, checksum


def seed_draft(
    settings: Settings,
    slug: str,
    media_id: str,
    **youtube: Any,
) -> PublishDraft:
    """Store a valid draft for ``media_id``, with optional YouTube overrides."""
    service = PublishingService(settings)
    media = service.get_media(slug, media_id)
    draft = service.seed_draft(slug, media)
    for field, value in youtube.items():
        setattr(draft.youtube, field, value)
    PublishingRepository(service.paths_for(slug)).save_draft(draft)
    return draft


def write_token_file(settings: Settings, *, scopes: list[str], expiry: str | None = None) -> Path:
    """A token file with the right *shape* and obviously fake values.

    Nothing here is or resembles a real credential; the point is only that
    ``Credentials.from_authorized_user_file`` can parse it and report the scopes.
    """
    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "token": "fake-access-token-for-tests",
        "refresh_token": "fake-refresh-token-for-tests",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "000000000000-testclient.apps.googleusercontent.com",
        "client_secret": "fake-client-secret-for-tests",
        "scopes": scopes,
        "universe_domain": "googleapis.com",
        "account": "",
    }
    if expiry is not None:
        payload["expiry"] = expiry
    target = directory / "youtube-upload-token.json"
    target.write_text(json.dumps(payload), "utf-8")
    target.chmod(0o600)
    return target


def write_client_file(
    settings: Settings,
    *,
    name: str = "client_secret_000000000000-test.apps.googleusercontent.com.json",
    installed: bool = True,
) -> Path:
    """An OAuth client file with the structure Google's download has."""
    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    body = {
        "client_id": "000000000000-testclient.apps.googleusercontent.com",
        "project_id": "evb-test",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "fake-client-secret-for-tests",
        "redirect_uris": ["http://localhost"],
    }
    payload = {"installed": body} if installed else {"web": body}
    target = directory / name
    target.write_text(json.dumps(payload), "utf-8")
    target.chmod(0o600)
    return target


class FakeCredentials:
    """The subset of ``google.oauth2.credentials.Credentials`` this app touches."""

    def __init__(self, *, scopes: list[str] | None = None, valid: bool = True) -> None:
        from app.publishing.youtube import SCOPES

        self.scopes = scopes if scopes is not None else list(SCOPES)
        self.valid = valid
        self.expired = not valid
        self.refresh_token = "fake-refresh-token-for-tests"
        self.refreshed = False

    def refresh(self, _request: Any) -> None:
        self.refreshed = True
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return json.dumps({"token": "fake", "scopes": self.scopes})


class FakeYouTubeClient:
    """Stands in for the API wrapper. Records what it was asked to do."""

    def __init__(
        self,
        _credentials: Any = None,
        *,
        chunks: int = 3,
        thumbnail_error: Exception | None = None,
        caption_error: Exception | None = None,
        upload_error: Exception | None = None,
        status: dict[str, Any] | None = None,
    ) -> None:
        self.chunks = chunks
        self.thumbnail_error = thumbnail_error
        self.caption_error = caption_error
        self.upload_error = upload_error
        self._status = status or {
            "uploadStatus": "uploaded",
            "privacyStatus": "private",
            "processingStatus": "processing",
            "publishAt": None,
        }
        self.upload_calls: list[dict[str, Any]] = []
        self.thumbnail_calls: list[tuple[str, Path]] = []
        self.caption_calls: list[tuple[str, Path]] = []
        self.status_calls: list[str] = []
        self.progress_reports: list[tuple[int, int]] = []

    def channel(self) -> dict[str, Any]:
        return {"id": "UC_test_channel", "title": "Vanished Earth", "thumbnailUrl": None}

    def upload_video(
        self,
        video: Path,
        *,
        body: dict[str, Any],
        notify_subscribers: bool,
        on_progress: Any = None,
        is_cancelled: Any = None,
    ) -> dict[str, Any]:
        if self.upload_error is not None:
            raise self.upload_error
        self.upload_calls.append(
            {"video": video, "body": body, "notifySubscribers": notify_subscribers}
        )
        total = video.stat().st_size
        for step in range(1, self.chunks + 1):
            if is_cancelled is not None and is_cancelled():
                from app.publishing.youtube import UploadCancelled

                raise UploadCancelled()
            sent = int(total * step / self.chunks)
            self.progress_reports.append((sent, total))
            if on_progress is not None:
                on_progress(sent, total)
        return {
            "id": "vid_test_0001",
            "status": {
                "privacyStatus": body["status"]["privacyStatus"],
                "uploadStatus": "uploaded",
                "publishAt": body["status"].get("publishAt"),
            },
        }

    def set_thumbnail(self, video_id: str, thumbnail: Path) -> None:
        if self.thumbnail_error is not None:
            raise self.thumbnail_error
        self.thumbnail_calls.append((video_id, thumbnail))

    def insert_caption(
        self, video_id: str, srt: Path, *, language: str = "en",
        name: str = "English", is_draft: bool = False,
    ) -> str:
        if self.caption_error is not None:
            raise self.caption_error
        self.caption_calls.append((video_id, srt))
        return "caption_track_0001"

    def video_status(self, video_id: str) -> dict[str, Any]:
        self.status_calls.append(video_id)
        return dict(self._status)


def install_fake_youtube(
    monkeypatch: Any, client: FakeYouTubeClient, *, scopes: list[str] | None = None
) -> FakeYouTubeClient:
    """Point the job manager at a fake client and a fake, always-usable grant."""
    import app.publishing.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "YouTubeClient", lambda _credentials: client)
    monkeypatch.setattr(
        jobs_module.YouTubeCredentials,
        "usable_credentials",
        lambda self: FakeCredentials(scopes=scopes),
    )
    return client


# --- Meta, TikTok and the hosting layer -------------------------------------
#
# Same rule as the YouTube fakes above: no test in this suite contacts Meta or
# TikTok, and none of the values here is or resembles a real credential. The
# fakes record what they were asked to do so a test can assert that a video was
# sent *once*, to *one* platform.


class FakeMediaHost:
    """Stands in for the S3/R2 layer. Records what was parked and removed."""

    def __init__(self, *, put_error: Exception | None = None) -> None:
        self.put_error = put_error
        self.put_calls: list[Path] = []
        self.deleted: list[str] = []

    def put(self, path: Path, *, key_hint: str) -> Any:
        from app.publishing.hosting import HostedMedia

        if self.put_error is not None:
            raise self.put_error
        self.put_calls.append(path)
        key = f"evb-test/{key_hint}"
        return HostedMedia(
            url=f"https://example-bucket.test/{key}?signature=fake",
            object_key=key,
            expires_at=datetime.now(timezone.utc),
        )

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)


class FakeMetaClient:
    """The Instagram and Facebook calls the job worker makes, and nothing else."""

    def __init__(
        self,
        *,
        container_states: list[str] | None = None,
        publish_error: Exception | None = None,
        container_error: Exception | None = None,
        facebook_states: list[str] | None = None,
        finish_error: Exception | None = None,
    ) -> None:
        self._container_states = container_states or ["FINISHED"]
        self._facebook_states = facebook_states or ["ready"]
        self.publish_error = publish_error
        self.container_error = container_error
        self.finish_error = finish_error
        self.container_calls: list[dict[str, Any]] = []
        self.publish_calls: list[str] = []
        self.fb_start_calls = 0
        self.fb_upload_calls: list[str] = []
        self.fb_finish_calls: list[tuple[str, str]] = []

    # Instagram
    def create_reel_container(self, *, video_url: str, caption: str, share_to_feed: bool) -> str:
        if self.container_error is not None:
            raise self.container_error
        self.container_calls.append(
            {"videoUrl": video_url, "caption": caption, "shareToFeed": share_to_feed}
        )
        return "ig_container_0001"

    def container_status(self, container_id: str) -> tuple[str, str | None]:
        state = self._container_states[0]
        if len(self._container_states) > 1:
            self._container_states = self._container_states[1:]
        return state, None

    def publish_container(self, container_id: str) -> str:
        if self.publish_error is not None:
            raise self.publish_error
        self.publish_calls.append(container_id)
        return "ig_media_0001"

    def media_permalink(self, media_id: str) -> str:
        return f"https://www.instagram.com/reel/{media_id}/"

    # Facebook
    def start_page_reel(self) -> tuple[str, str]:
        self.fb_start_calls += 1
        return "fb_video_0001", "https://rupload.test/fb_video_0001"

    def upload_page_reel(self, upload_url: str, *, video_url: str) -> None:
        self.fb_upload_calls.append(video_url)

    def page_reel_status(self, video_id: str) -> tuple[str, str | None]:
        state = self._facebook_states[0]
        if len(self._facebook_states) > 1:
            self._facebook_states = self._facebook_states[1:]
        return state, None

    def finish_page_reel(self, video_id: str, *, description: str) -> None:
        if self.finish_error is not None:
            raise self.finish_error
        self.fb_finish_calls.append((video_id, description))

    def page_reel_permalink(self, video_id: str) -> str:
        return f"https://www.facebook.com/reel/{video_id}"


class FakeTikTokClient:
    """Creator info, Direct Post init, chunked upload and the status poll."""

    def __init__(
        self,
        *,
        privacy_options: list[str] | None = None,
        statuses: list[str] | None = None,
        post_ids: list[str] | None = None,
        fail_reason: str = "",
        init_error: Exception | None = None,
    ) -> None:
        self.privacy_options = (
            privacy_options if privacy_options is not None else ["SELF_ONLY"]
        )
        self._statuses = statuses or ["PUBLISH_COMPLETE"]
        self.post_ids = post_ids if post_ids is not None else []
        self.fail_reason = fail_reason
        self.init_error = init_error
        self.init_calls: list[dict[str, Any]] = []
        self.upload_calls: list[Path] = []
        self.progress_reports: list[tuple[int, int]] = []

    def creator_info(self) -> dict[str, Any]:
        return {
            "nickname": "Vanished Earth Docs",
            "username": "vanishedearthdocs",
            "avatarUrl": None,
            "privacyLevelOptions": list(self.privacy_options),
            "commentDisabled": False,
            "duetDisabled": False,
            "stitchDisabled": False,
            "maxVideoPostDurationSeconds": 600,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
        }

    def init_direct_post(self, video: Path, **kwargs: Any) -> tuple[str, str, int]:
        if self.init_error is not None:
            raise self.init_error
        self.init_calls.append({"video": video, **kwargs})
        return "tt_publish_0001", "https://upload.tiktok.test/tt_publish_0001", 5_242_880

    def upload_video(
        self,
        upload_url: str,
        video: Path,
        *,
        chunk_size: int,
        on_progress: Any = None,
        is_cancelled: Any = None,
    ) -> None:
        self.upload_calls.append(video)
        total = video.stat().st_size
        if is_cancelled is not None and is_cancelled():
            from app.publishing.youtube import UploadCancelled

            raise UploadCancelled()
        self.progress_reports.append((total, total))
        if on_progress is not None:
            on_progress(total, total)

    def publish_status(self, publish_id: str) -> dict[str, Any]:
        state = self._statuses[0]
        if len(self._statuses) > 1:
            self._statuses = self._statuses[1:]
        return {"status": state, "failReason": self.fail_reason, "postIds": list(self.post_ids)}


def meta_target(
    *,
    page_id: str = "111222333",
    page_name: str = "Vanished Earth Docs",
    instagram_id: str | None = "444555666",
    instagram_username: str | None = "vanishedearthdocs",
) -> Any:
    from app.publishing.meta import MetaTarget

    return MetaTarget(
        page_id=page_id,
        page_name=page_name,
        page_token="fake-page-token-for-tests",
        instagram_id=instagram_id,
        instagram_username=instagram_username,
    )


def install_fake_meta(
    monkeypatch: Any,
    client: FakeMetaClient,
    host: FakeMediaHost,
    *,
    target: Any = None,
) -> tuple[FakeMetaClient, FakeMediaHost]:
    """Point the job manager at a fake Graph client and a fake bucket."""
    import app.publishing.jobs as jobs_module

    resolved = target if target is not None else meta_target()
    monkeypatch.setattr(jobs_module.MetaCredentials, "target", lambda self: resolved)
    monkeypatch.setattr(
        jobs_module.PublishJobManager, "_meta_client", lambda self, _target: client
    )
    monkeypatch.setattr(jobs_module, "resolve_media_host", lambda _settings=None: host)
    # Polling is real code with a real sleep; tests must not wait on it.
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _seconds: None)
    return client, host


def install_fake_tiktok(monkeypatch: Any, client: FakeTikTokClient) -> FakeTikTokClient:
    """Point the job manager at a fake TikTok client and a usable grant."""
    import app.publishing.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module.TikTokCredentials, "access_token", lambda self: "fake-tiktok-token"
    )
    monkeypatch.setattr(
        jobs_module.PublishJobManager, "_tiktok_client", lambda self, _token: client
    )
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _seconds: None)
    return client


def write_meta_token(
    settings: Settings,
    *,
    pages: list[dict[str, Any]] | None = None,
    selected: str | None = "111222333",
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> Path:
    """A Meta grant file with the right shape and obviously fake values."""
    from app.publishing.meta import SCOPES, TOKEN_FILENAME

    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "userToken": "fake-user-token-for-tests",
        "expiresAt": expires_at,
        "scopes": scopes if scopes is not None else list(SCOPES),
        "pages": pages
        if pages is not None
        else [
            {
                "id": "111222333",
                "name": "Vanished Earth Docs",
                "accessToken": "fake-page-token-for-tests",
                "instagramId": "444555666",
                "instagramUsername": "vanishedearthdocs",
            }
        ],
        "selectedPageId": selected,
        "connectedAt": datetime.now(timezone.utc).isoformat(),
    }
    target = directory / TOKEN_FILENAME
    target.write_text(json.dumps(payload), "utf-8")
    target.chmod(0o600)
    return target


def write_tiktok_token(
    settings: Settings,
    *,
    scopes: list[str] | None = None,
    expires_at: str | None = None,
    creator_info: dict[str, Any] | None = None,
) -> Path:
    from app.publishing.tiktok import SCOPES, TOKEN_FILENAME

    directory = settings.oauth_secrets_dir
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "accessToken": "fake-tiktok-access-token",
        "refreshToken": "fake-tiktok-refresh-token",
        "openId": "fake-open-id",
        "scopes": scopes if scopes is not None else list(SCOPES),
        "expiresAt": expires_at,
        "refreshExpiresAt": None,
        "connectedAt": datetime.now(timezone.utc).isoformat(),
    }
    if creator_info is not None:
        payload["creatorInfo"] = creator_info
    target = directory / TOKEN_FILENAME
    target.write_text(json.dumps(payload), "utf-8")
    target.chmod(0o600)
    return target


def store_meta_app(settings: Settings) -> None:
    """Obviously-fake application credentials with the right *shape*."""
    settings.set_secret("meta_app_id", "1234567890123456")
    settings.set_secret("meta_app_secret", "fakemetaappsecretfortests0000")


def store_tiktok_app(settings: Settings) -> None:
    settings.set_secret("tiktok_client_key", "awfaketiktokkey123")
    settings.set_secret("tiktok_client_secret", "fakeTikTokClientSecretForTests")


def future_local(days: int = 2, hour: int = 22) -> str:
    """A local Istanbul wall-clock value comfortably in the future."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    when = datetime.now(ZoneInfo("Europe/Istanbul")) + timedelta(days=days)
    return when.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def past_local(days: int = 2) -> str:
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    when = datetime.now(ZoneInfo("Europe/Istanbul")) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M")


def draft_for(settings: Settings, slug: str, media_id: str) -> PublishDraft:
    return PublishingRepository(PublishingService(settings).paths_for(slug)).get_draft(media_id)


__all__ = [
    "FakeCredentials",
    "FakeMediaHost",
    "FakeMetaClient",
    "FakeTikTokClient",
    "FakeYouTubeClient",
    "JPEG_BYTES",
    "PNG_BYTES",
    "PublishMode",
    "SRT_TEXT",
    "SourceFingerprint",
    "add_long_render",
    "add_short",
    "draft_for",
    "future_local",
    "install_fake_meta",
    "install_fake_tiktok",
    "install_fake_youtube",
    "make_project",
    "meta_target",
    "past_local",
    "seed_draft",
    "store_meta_app",
    "store_tiktok_app",
    "write_client_file",
    "write_meta_token",
    "write_tiktok_token",
    "write_token_file",
]
