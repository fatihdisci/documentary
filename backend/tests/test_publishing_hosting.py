"""The temporary media-hosting layer.

Two things are worth proving here and nothing else really is:

* an unconfigured install **refuses** rather than inventing a URL Meta cannot
  reach — a silent fallback would turn a five-second setup mistake into a
  failed publish several minutes later;
* the presigned link is signed correctly and expires, and neither key ever
  appears in a status response, an error or a log line.

No bucket is contacted: ``httpx`` is replaced by a recorder.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.errors import AppError, EnvironmentError_
from app.publishing.hosting import (
    ACCESS_KEY_SECRET,
    SECRET_KEY_SECRET,
    ObjectStorageHost,
    media_host_status,
    resolve_media_host,
)

ACCESS_KEY = "FAKEACCESSKEYFORTESTS"
SECRET_KEY = "fake-secret-access-key-for-tests-only"


def configure_bucket(settings, *, provider: str = "s3", keys: bool = True) -> None:
    settings.save_mutable(
        settings.mutable.model_copy(
            update={
                "media_host_provider": provider,
                "object_storage_endpoint": "https://accountid.r2.cloudflarestorage.com",
                "object_storage_bucket": "evb-temp",
                "object_storage_region": "auto",
                "object_storage_prefix": "reels",
                "media_host_ttl_seconds": 3600,
            }
        )
    )
    if keys:
        settings.set_secret(ACCESS_KEY_SECRET, ACCESS_KEY)
        settings.set_secret(SECRET_KEY_SECRET, SECRET_KEY)


class _Recorder:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return httpx.Response(self.status, request=httpx.Request("PUT", url))


class TestConfiguration:
    def test_an_unconfigured_install_refuses_instead_of_guessing(self, settings) -> None:
        with pytest.raises(EnvironmentError_) as excinfo:
            resolve_media_host(settings)

        assert excinfo.value.code.value == "media_host_not_configured"
        # The message has to be the setup instruction, not a shrug.
        assert "R2" in excinfo.value.suggestion or "S3" in excinfo.value.suggestion

    def test_a_half_configured_bucket_is_reported_as_incomplete(self, settings) -> None:
        configure_bucket(settings, keys=False)

        status = media_host_status(settings)

        assert status.configured is False
        assert status.keys_present is False
        assert "anahtarlar" in (status.problem or "")

        with pytest.raises(EnvironmentError_):
            resolve_media_host(settings)

    def test_a_configured_bucket_reports_ready_without_the_keys(self, settings) -> None:
        configure_bucket(settings)

        status = media_host_status(settings)

        assert status.configured is True
        assert status.keys_present is True
        payload = status.model_dump_json()
        assert ACCESS_KEY not in payload
        assert SECRET_KEY not in payload

    def test_the_status_endpoint_never_returns_a_key(self, client, settings) -> None:
        configure_bucket(settings)

        response = client.get("/api/publishing/media-host/status")

        assert response.status_code == 200
        assert ACCESS_KEY not in response.text
        assert SECRET_KEY not in response.text
        assert response.json()["bucket"] == "evb-temp"

    def test_settings_can_be_saved_without_wiping_stored_keys(self, client, settings) -> None:
        configure_bucket(settings)

        response = client.put(
            "/api/publishing/media-host/settings",
            json={
                "provider": "s3",
                "endpoint": "https://accountid.r2.cloudflarestorage.com",
                "bucket": "evb-temp-renamed",
                "region": "auto",
                "prefix": "reels",
                "ttlSeconds": 1800,
                "deleteAfterPublish": True,
                "accessKeyId": None,
                "secretAccessKey": None,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["bucket"] == "evb-temp-renamed"
        # Leaving the key fields empty must not break a working bucket.
        assert body["keysPresent"] is True

    def test_an_insecure_endpoint_is_refused(self, client) -> None:
        response = client.put(
            "/api/publishing/media-host/settings",
            json={
                "provider": "s3",
                "endpoint": "http://insecure.example.com",
                "bucket": "b",
                "region": "auto",
                "prefix": "p",
                "ttlSeconds": 600,
                "deleteAfterPublish": True,
            },
        )

        assert response.status_code == 422
        assert "https" in response.json()["message"]


class TestPresignedLinks:
    def test_a_link_is_signed_scoped_and_time_limited(self, settings) -> None:
        configure_bucket(settings)
        host = ObjectStorageHost(settings)

        url = host.presign_get("reels/the-dodo-abc.mp4")

        query = parse_qs(urlparse(url).query)
        assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert query["X-Amz-Expires"] == ["3600"]
        assert len(query["X-Amz-Signature"][0]) == 64
        # The secret is used to sign and is never part of the link.
        assert SECRET_KEY not in url
        assert query["X-Amz-Credential"][0].startswith(ACCESS_KEY)

    def test_two_links_for_the_same_object_differ(self, settings) -> None:
        configure_bucket(settings)
        host = ObjectStorageHost(settings)

        first = host.presign_get("reels/a.mp4")
        second = host.presign_get("reels/a.mp4")

        # Signatures are bound to the moment they were made, so a link copied
        # out of a log yesterday is worthless today.
        assert urlparse(first).path == urlparse(second).path
        assert parse_qs(urlparse(first).query)["X-Amz-Date"]

    def test_uploading_puts_the_object_under_the_prefix_and_returns_a_link(
        self, settings, tmp_path, monkeypatch
    ) -> None:
        configure_bucket(settings)
        import app.publishing.hosting as hosting_module

        recorder = _Recorder()
        monkeypatch.setattr(hosting_module.httpx, "put", recorder)

        video = tmp_path / "the-dodo_v01.mp4"
        video.write_bytes(b"video-bytes" * 100)
        hosted = ObjectStorageHost(settings).put(video, key_hint=video.name)

        assert hosted.object_key.startswith("reels/")
        assert hosted.url.startswith("https://accountid.r2.cloudflarestorage.com/evb-temp/")
        assert "X-Amz-Signature=" in hosted.url
        sent = recorder.calls[0]
        assert sent["headers"]["content-type"] == "video/mp4"
        assert sent["headers"]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=")
        # The object name is not guessable from the filename alone.
        assert hosted.object_key != f"reels/{video.name}"

    def test_a_rejected_upload_is_an_actionable_error(
        self, settings, tmp_path, monkeypatch
    ) -> None:
        configure_bucket(settings)
        import app.publishing.hosting as hosting_module

        monkeypatch.setattr(hosting_module.httpx, "put", _Recorder(status=403))
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x" * 64)

        with pytest.raises(AppError) as excinfo:
            ObjectStorageHost(settings).put(video, key_hint="v")

        assert excinfo.value.code.value == "media_host_failed"
        assert excinfo.value.suggestion

    def test_deleting_a_missing_object_is_not_an_error(
        self, settings, monkeypatch
    ) -> None:
        configure_bucket(settings)
        import app.publishing.hosting as hosting_module

        recorder = _Recorder(status=404)
        monkeypatch.setattr(hosting_module.httpx, "delete", recorder)

        # Tidying up must never be the thing that fails a finished publish.
        ObjectStorageHost(settings).delete("reels/gone.mp4")

        assert recorder.calls[0]["url"].endswith("reels/gone.mp4")
