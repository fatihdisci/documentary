"""The TikTok connection: credentials, PKCE, the audit constraint, chunking.

No test here reaches TikTok. The one behaviour worth stating up front is the
audit: an unaudited app may only post privately, and these tests exist to prove
the app *says* that rather than offering a public option that would be refused.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.errors import AppError, ValidationError
from app.publishing.tiktok import (
    MAX_SINGLE_CHUNK_BYTES,
    MIN_CHUNK_BYTES,
    SCOPES,
    TikTokClient,
    TikTokCredentials,
    plan_chunks,
)
from tests.publishing_factories import store_tiktok_app, write_tiktok_token


class _Recorder:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        reply = self._responses.pop(0) if self._responses else {}
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply, request=httpx.Request("POST", url))


def _install(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> _Recorder:
    import app.publishing.tiktok as tiktok_module

    recorder = _Recorder(responses)
    monkeypatch.setattr(tiktok_module.httpx, "post", recorder)
    monkeypatch.setattr(tiktok_module.httpx, "put", recorder)
    return recorder


class TestAppCredentials:
    def test_a_fresh_install_reports_no_application(self, settings) -> None:
        connection = TikTokCredentials(settings).status()

        assert connection.app_configured is False
        assert connection.connected is False
        assert connection.audit_required is True

    def test_the_client_secret_never_leaves_the_backend(self, client, settings) -> None:
        store_tiktok_app(settings)

        response = client.get("/api/publishing/tiktok/status")

        assert response.status_code == 200
        assert "fakeTikTokClientSecretForTests" not in response.text
        assert "awfaketiktokkey123" not in response.text
        assert response.json()["appConfigured"] is True

    def test_a_second_write_is_refused_unless_it_was_meant(self, settings) -> None:
        store = TikTokCredentials(settings)
        store.store_app_credentials("awkeyone123456", "secretoneforthetests00", replace=False)

        with pytest.raises(AppError):
            store.store_app_credentials(
                "awkeytwo123456", "secrettwoforthetests00", replace=False
            )

        assert settings.get_secret("tiktok_client_key") == "awkeyone123456"

    def test_a_malformed_key_is_refused_before_it_is_stored(self, settings) -> None:
        with pytest.raises(ValidationError):
            TikTokCredentials(settings).store_app_credentials("no", "short", replace=False)

        assert settings.get_secret("tiktok_client_key") is None


class TestAuthorization:
    def test_connecting_without_an_application_says_so(self, settings) -> None:
        with pytest.raises(AppError) as excinfo:
            TikTokCredentials(settings).start_authorization()

        assert excinfo.value.code.value == "tiktok_app_missing"

    def test_the_authorization_url_asks_for_publishing_and_uses_pkce(
        self, settings
    ) -> None:
        store_tiktok_app(settings)
        store = TikTokCredentials(settings)

        start = store.start_authorization()

        query = parse_qs(urlparse(start.authorization_url).query)
        assert "video.publish" in query["scope"][0]
        assert query["code_challenge_method"] == ["S256"]
        # The challenge must be the SHA-256 of the verifier this run kept, or
        # TikTok will refuse the exchange.
        state = query["state"][0]
        verifier = store._pending[state].verifier
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        assert query["code_challenge"] == [expected]
        # The verifier itself is never in the URL.
        assert verifier not in start.authorization_url

    def test_a_callback_with_an_unknown_state_is_refused(self, settings) -> None:
        store_tiktok_app(settings)

        with pytest.raises(AppError) as excinfo:
            TikTokCredentials(settings).complete_authorization("code", "never-issued")

        assert excinfo.value.code.value == "tiktok_auth_failed"

    def test_a_completed_flow_stores_a_grant_that_is_owner_readable_only(
        self, settings, monkeypatch
    ) -> None:
        store_tiktok_app(settings)
        store = TikTokCredentials(settings)
        start = store.start_authorization()
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]

        _install(
            monkeypatch,
            [
                {
                    "access_token": "fake-access",
                    "refresh_token": "fake-refresh",
                    "expires_in": 86400,
                    "refresh_expires_in": 31536000,
                    "open_id": "fake-open-id",
                    "scope": "user.info.basic,video.publish",
                },
                # The status call then reads creator info.
                {
                    "data": {
                        "creator_nickname": "Vanished Earth Docs",
                        "creator_username": "vanishedearthdocs",
                        "privacy_level_options": ["SELF_ONLY"],
                        "max_video_post_duration_sec": 600,
                    }
                },
            ],
        )

        connection = store.complete_authorization("the-code", state)

        assert connection.connected is True
        assert connection.scopes_sufficient is True
        assert store.token_file.stat().st_mode & 0o777 == 0o600
        assert "fake-access" not in connection.model_dump_json()
        assert "fake-refresh" not in connection.model_dump_json()

    def test_a_refused_exchange_is_reported_without_the_secret(
        self, settings, monkeypatch
    ) -> None:
        store_tiktok_app(settings)
        store = TikTokCredentials(settings)
        start = store.start_authorization()
        state = parse_qs(urlparse(start.authorization_url).query)["state"][0]
        _install(
            monkeypatch,
            [
                httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "code expired"},
                    request=httpx.Request("POST", "https://open.tiktokapis.com/"),
                )
            ],
        )

        with pytest.raises(AppError) as excinfo:
            store.complete_authorization("stale", state)

        assert excinfo.value.code.value == "tiktok_auth_failed"
        assert "fakeTikTokClientSecretForTests" not in (excinfo.value.details or "")


class TestAuditHonesty:
    def test_only_self_only_means_the_app_is_unaudited_and_says_so(
        self, settings, monkeypatch
    ) -> None:
        store_tiktok_app(settings)
        write_tiktok_token(
            settings,
            creator_info={
                "nickname": "Vanished Earth Docs",
                "username": "vanishedearthdocs",
                "privacyLevelOptions": ["SELF_ONLY"],
            },
        )

        connection = TikTokCredentials(settings).status()

        assert connection.connected is True
        assert connection.audit_required is True
        assert "denetlenmemiş" in (connection.problem or "")
        assert "audit" in (connection.suggestion or "")
        assert "denetimden geçmediği" in connection.status_message
        # And it never claims the post will be public.
        assert "herkese açık" not in connection.status_message.casefold()

    def test_an_audited_app_offers_the_wider_options(self, settings) -> None:
        store_tiktok_app(settings)
        write_tiktok_token(
            settings,
            creator_info={
                "nickname": "Vanished Earth Docs",
                "username": "vanishedearthdocs",
                "privacyLevelOptions": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
            },
        )

        connection = TikTokCredentials(settings).status()

        assert connection.audit_required is False
        assert connection.problem is None

    def test_missing_scopes_ask_for_a_reconnect(self, settings) -> None:
        store_tiktok_app(settings)
        write_tiktok_token(settings, scopes=["user.info.basic"])

        connection = TikTokCredentials(settings).status()

        assert connection.needs_reconnect is True
        assert "video.publish" in connection.missing_scopes

    def test_publishing_without_the_publish_scope_is_refused(self, settings) -> None:
        store_tiktok_app(settings)
        write_tiktok_token(settings, scopes=["user.info.basic"])

        with pytest.raises(AppError) as excinfo:
            TikTokCredentials(settings).access_token()

        assert excinfo.value.code.value == "tiktok_scope_missing"


class TestPosting:
    def test_the_file_is_sent_directly_and_never_hosted(
        self, settings, tmp_path, monkeypatch
    ) -> None:
        video = tmp_path / "reel.mp4"
        video.write_bytes(b"x" * 2048)
        recorder = _install(
            monkeypatch,
            [
                {"data": {"publish_id": "pub-1", "upload_url": "https://upload.test/pub-1"}},
                httpx.Response(200, request=httpx.Request("PUT", "https://upload.test/pub-1")),
            ],
        )
        client = TikTokClient("fake-token", settings)

        publish_id, upload_url, chunk = client.init_direct_post(
            video,
            title="A dodo",
            privacy_level="SELF_ONLY",
            disable_comment=False,
            disable_duet=True,
            disable_stitch=True,
        )
        client.upload_video(upload_url, video, chunk_size=chunk)

        assert publish_id == "pub-1"
        init_body = recorder.calls[0]["content"].decode("utf-8")
        # FILE_UPLOAD, not PULL_FROM_URL: nothing is put on the internet.
        assert '"source": "FILE_UPLOAD"' in init_body
        assert "PULL_FROM_URL" not in init_body
        assert recorder.calls[1]["headers"]["Content-Range"] == "bytes 0-2047/2048"

    def test_a_refused_privacy_level_is_named_and_explained(
        self, settings, monkeypatch
    ) -> None:
        _install(
            monkeypatch,
            [
                httpx.Response(
                    403,
                    json={
                        "error": {
                            "code": "unaudited_client_can_only_post_to_private_accounts",
                            "message": "unaudited",
                            "log_id": "abc",
                        }
                    },
                    request=httpx.Request("POST", "https://open.tiktokapis.com/"),
                )
            ],
        )
        client = TikTokClient("fake-token", settings)

        with pytest.raises(AppError) as excinfo:
            client.creator_info()

        assert excinfo.value.code.value == "tiktok_unaudited"
        assert "hesabın kendisi" in excinfo.value.message
        assert "Gizli hesap" in excinfo.value.suggestion

    def test_a_failure_reported_inside_a_200_is_still_a_failure(
        self, settings, monkeypatch
    ) -> None:
        # TikTok answers 200 with an error body, so the status line alone is not
        # enough to decide the call worked.
        _install(
            monkeypatch,
            [
                httpx.Response(
                    200,
                    json={"error": {"code": "spam_risk_too_many_posts", "message": "slow down"}},
                    request=httpx.Request("POST", "https://open.tiktokapis.com/"),
                )
            ],
        )

        with pytest.raises(AppError) as excinfo:
            TikTokClient("fake-token", settings).creator_info()

        assert excinfo.value.http_status == 429


class TestChunkPlanning:
    def test_a_small_file_is_one_chunk(self) -> None:
        assert plan_chunks(1_000_000) == (1_000_000, 1)

    def test_the_boundary_is_still_one_chunk(self) -> None:
        assert plan_chunks(MAX_SINGLE_CHUNK_BYTES) == (MAX_SINGLE_CHUNK_BYTES, 1)

    def test_a_large_file_uses_chunks_within_tiktoks_limits(self) -> None:
        size = 250 * 1024 * 1024
        chunk, count = plan_chunks(size)

        assert chunk >= MIN_CHUNK_BYTES
        assert chunk <= MAX_SINGLE_CHUNK_BYTES
        assert count >= 1
        # Every chunk but the last is exactly ``chunk``; the last absorbs the
        # remainder, so the plan must never demand more chunks than there are.
        assert chunk * count <= size


class TestEndpoints:
    def test_the_callback_reports_a_refusal_as_a_page(self, client) -> None:
        response = client.get(
            "/api/publishing/tiktok/callback",
            params={"error": "access_denied"},
        )

        assert response.status_code == 400
        assert "tamamlanmadı" in response.text

    def test_scopes_are_exactly_what_the_app_needs(self) -> None:
        assert set(SCOPES) == {"user.info.basic", "video.publish"}
