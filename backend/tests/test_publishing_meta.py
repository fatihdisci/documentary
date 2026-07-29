"""The Meta connection: credentials, OAuth, page discovery and error mapping.

Not one test here reaches the network. Every Graph call is served by a stub
transport, and every credential is an obviously fake string with the right
shape. The point of most of these tests is the *negative* one — that the App
Secret and the access tokens do not appear in any response, and that a broken
connection produces a message the user can act on rather than a stack trace.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.errors import AppError, ValidationError
from app.publishing.meta import (
    SCOPES,
    MetaClient,
    MetaCredentials,
    map_graph_error,
)
from tests.publishing_factories import (
    meta_target,
    store_meta_app,
    write_meta_token,
)


class _Recorder:
    """Stands in for ``httpx.get``/``httpx.post``. Records and replies."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        reply = self._responses.pop(0) if self._responses else {}
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply, request=httpx.Request("GET", url))


def _install(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> _Recorder:
    import app.publishing.meta as meta_module

    recorder = _Recorder(responses)
    monkeypatch.setattr(meta_module.httpx, "get", recorder)
    monkeypatch.setattr(meta_module.httpx, "post", recorder)
    return recorder


# --- application credentials ------------------------------------------------


class TestAppCredentials:
    def test_a_fresh_install_reports_no_application(self, settings) -> None:
        connection = MetaCredentials(settings).status()

        assert connection.app_configured is False
        assert connection.token_present is False
        assert connection.connected is False
        assert "girilmedi" in connection.status_message

    def test_storing_the_pair_makes_it_configured_but_not_readable(self, settings) -> None:
        store = MetaCredentials(settings)
        store.store_app_credentials(
            "1234567890123456", "fakemetaappsecretfortests0000", replace=False
        )

        connection = store.status()
        assert connection.app_configured is True
        # The whole point: neither value is anywhere in the wire model.
        payload = connection.model_dump_json()
        assert "1234567890123456" not in payload
        assert "fakemetaappsecretfortests0000" not in payload

    def test_the_secret_never_reaches_the_settings_response(self, client, settings) -> None:
        store_meta_app(settings)

        response = client.get("/api/settings")

        assert response.status_code == 200
        body = response.text
        assert "fakemetaappsecretfortests0000" not in body
        assert "1234567890123456" not in body
        # Presence is reported; the value is not.
        assert "meta_app_secret" in response.json()["configuredSecrets"]

    def test_a_second_write_is_refused_unless_it_was_meant(self, settings) -> None:
        store = MetaCredentials(settings)
        store.store_app_credentials("1234567890123456", "firstsecretvaluehere00", replace=False)

        with pytest.raises(AppError) as excinfo:
            store.store_app_credentials(
                "9999999999999999", "secondsecretvaluehere0", replace=False
            )

        assert excinfo.value.http_status == 409
        # The stored pair is untouched, so a working connection is not broken by
        # a stray save.
        assert settings.get_secret("meta_app_id") == "1234567890123456"

        store.store_app_credentials("9999999999999999", "secondsecretvaluehere0", replace=True)
        assert settings.get_secret("meta_app_id") == "9999999999999999"

    @pytest.mark.parametrize(
        ("app_id", "app_secret"),
        [
            ("not-a-number", "fakemetaappsecretfortests0000"),
            ("1234567890123456", "short"),
            ("1234567890123456", "has spaces in it and is long"),
        ],
    )
    def test_a_malformed_pair_is_refused_before_it_is_stored(
        self, settings, app_id: str, app_secret: str
    ) -> None:
        with pytest.raises(ValidationError):
            MetaCredentials(settings).store_app_credentials(
                app_id, app_secret, replace=False
            )

        assert settings.get_secret("meta_app_id") is None
        assert settings.get_secret("meta_app_secret") is None


# --- the authorization URL --------------------------------------------------


class TestAuthorization:
    def test_connecting_without_an_application_says_so(self, settings) -> None:
        with pytest.raises(AppError) as excinfo:
            MetaCredentials(settings).start_authorization()

        assert excinfo.value.code.value == "meta_app_missing"

    def test_the_authorization_url_carries_every_needed_scope(self, settings) -> None:
        store_meta_app(settings)

        start = MetaCredentials(settings).start_authorization()

        assert start.authorization_url.startswith("https://www.facebook.com/")
        for scope in SCOPES:
            assert scope in start.authorization_url
        assert "response_type=code" in start.authorization_url
        # The redirect must be reported verbatim: the user pastes it into Meta.
        assert start.redirect_uri in start.authorization_url.replace("%3A", ":").replace(
            "%2F", "/"
        )

    def test_the_default_redirect_is_this_backend_on_loopback(self, settings) -> None:
        store_meta_app(settings)

        redirect = MetaCredentials(settings).redirect_uri

        assert redirect == (
            f"http://localhost:{settings.port}/api/publishing/meta/callback"
        )

    def test_a_callback_with_an_unknown_state_is_refused(self, settings) -> None:
        store_meta_app(settings)

        with pytest.raises(AppError) as excinfo:
            MetaCredentials(settings).complete_authorization("some-code", "never-issued")

        assert excinfo.value.code.value == "meta_auth_failed"

    def test_a_completed_flow_stores_the_grant_and_finds_the_accounts(
        self, settings, monkeypatch
    ) -> None:
        store_meta_app(settings)
        store = MetaCredentials(settings)
        start = store.start_authorization()
        state = start.authorization_url.split("state=")[1].split("&")[0]

        _install(
            monkeypatch,
            [
                {"access_token": "short-lived-fake"},
                {"access_token": "long-lived-fake", "expires_in": 5_184_000},
                {"data": [{"permission": scope, "status": "granted"} for scope in SCOPES]},
                {
                    "data": [
                        {
                            "id": "111222333",
                            "name": "Vanished Earth Docs",
                            "access_token": "page-token-fake",
                            "instagram_business_account": {
                                "id": "444555666",
                                "username": "vanishedearthdocs",
                            },
                        }
                    ]
                },
            ],
        )

        connection = store.complete_authorization("the-code", state)

        assert connection.connected is True
        assert connection.page_name == "Vanished Earth Docs"
        assert connection.instagram_username == "vanishedearthdocs"
        assert connection.scopes_sufficient is True
        # Neither token is anywhere in what the panel receives.
        payload = connection.model_dump_json()
        assert "long-lived-fake" not in payload
        assert "page-token-fake" not in payload

    def test_the_stored_grant_is_owner_readable_only(self, settings, monkeypatch) -> None:
        store_meta_app(settings)
        write_meta_token(settings)

        mode = MetaCredentials(settings).token_file.stat().st_mode & 0o777

        assert mode == 0o600

    def test_a_connection_without_a_page_is_an_actionable_error(
        self, settings, monkeypatch
    ) -> None:
        store_meta_app(settings)
        store = MetaCredentials(settings)
        start = store.start_authorization()
        state = start.authorization_url.split("state=")[1].split("&")[0]

        _install(
            monkeypatch,
            [
                {"access_token": "short-lived-fake"},
                {"access_token": "long-lived-fake", "expires_in": 100},
                {"data": []},
                {"data": []},
            ],
        )

        with pytest.raises(AppError) as excinfo:
            store.complete_authorization("the-code", state)

        assert excinfo.value.code.value == "meta_page_not_found"
        assert excinfo.value.suggestion


# --- status and target ------------------------------------------------------


class TestStatus:
    def test_several_pages_wait_for_a_choice(self, settings) -> None:
        store_meta_app(settings)
        write_meta_token(
            settings,
            selected=None,
            pages=[
                {
                    "id": "111",
                    "name": "Vanished Earth Docs",
                    "accessToken": "fake",
                    "instagramId": "444",
                    "instagramUsername": "vanishedearthdocs",
                },
                {
                    "id": "222",
                    "name": "Another Page",
                    "accessToken": "fake",
                    "instagramId": None,
                    "instagramUsername": None,
                },
            ],
        )
        store = MetaCredentials(settings)

        connection = store.status()
        assert connection.connected is False
        assert len(connection.pages) == 2
        assert "sayfa seçilmedi" in connection.status_message

        chosen = store.select_page("222")
        assert chosen.selected_page_id == "222"
        assert chosen.page_name == "Another Page"
        # No Instagram on that page, so publishing there is refused with a
        # message rather than attempted.
        assert chosen.problem is not None

    def test_missing_permissions_ask_for_a_reconnect(self, settings) -> None:
        store_meta_app(settings)
        write_meta_token(settings, scopes=["instagram_basic", "pages_show_list"])

        connection = MetaCredentials(settings).status()

        assert connection.needs_reconnect is True
        assert connection.scopes_sufficient is False
        assert "instagram_content_publish" in connection.missing_scopes
        assert "pages_manage_posts" in connection.missing_scopes

    def test_publishing_without_a_grant_is_refused(self, settings) -> None:
        store_meta_app(settings)

        with pytest.raises(AppError) as excinfo:
            MetaCredentials(settings).target()

        assert excinfo.value.code.value == "meta_auth_required"
        assert excinfo.value.http_status == 401

    def test_an_expired_grant_is_refused_rather_than_used(self, settings) -> None:
        store_meta_app(settings)
        write_meta_token(settings, expires_at="2020-01-01T00:00:00+00:00")

        with pytest.raises(AppError) as excinfo:
            MetaCredentials(settings).target()

        assert excinfo.value.code.value == "meta_auth_required"

    def test_a_page_without_instagram_refuses_only_instagram(self, settings) -> None:
        store_meta_app(settings)
        write_meta_token(
            settings,
            pages=[
                {
                    "id": "111222333",
                    "name": "Vanished Earth Docs",
                    "accessToken": "fake-page-token",
                    "instagramId": None,
                    "instagramUsername": None,
                }
            ],
        )

        target = MetaCredentials(settings).target()

        # The Facebook path is perfectly usable…
        assert target.page_id == "111222333"
        # …and only the Instagram one is blocked, with a fixable message.
        with pytest.raises(AppError) as excinfo:
            target.require_instagram()
        assert excinfo.value.code.value == "meta_instagram_not_linked"

    def test_disconnecting_keeps_the_application_credentials(self, settings) -> None:
        store_meta_app(settings)
        write_meta_token(settings)
        store = MetaCredentials(settings)

        connection = store.disconnect()

        assert connection.token_present is False
        assert connection.app_configured is True
        assert store.token_file.exists() is False


# --- publishing calls -------------------------------------------------------


class TestClientCalls:
    def test_a_reel_container_is_created_from_a_url_never_a_path(
        self, settings, monkeypatch
    ) -> None:
        store_meta_app(settings)
        recorder = _install(monkeypatch, [{"id": "container-1"}])
        client = MetaClient(meta_target(), settings)

        container = client.create_reel_container(
            video_url="https://bucket.test/video.mp4?sig=x",
            caption="A dodo.\n\n#dodo",
            share_to_feed=True,
        )

        assert container == "container-1"
        sent = recorder.calls[0]["data"]
        assert sent["media_type"] == "REELS"
        assert sent["video_url"] == "https://bucket.test/video.mp4?sig=x"
        assert sent["share_to_feed"] == "true"
        # A local path would be useless to Meta and is never constructed.
        assert not str(sent["video_url"]).startswith("/")

    def test_publishing_uses_the_container_and_returns_a_media_id(
        self, settings, monkeypatch
    ) -> None:
        store_meta_app(settings)
        recorder = _install(monkeypatch, [{"id": "media-9"}])
        client = MetaClient(meta_target(), settings)

        assert client.publish_container("container-1") == "media-9"
        assert recorder.calls[0]["data"]["creation_id"] == "container-1"

    def test_a_facebook_reel_is_started_then_finished(self, settings, monkeypatch) -> None:
        store_meta_app(settings)
        recorder = _install(
            monkeypatch,
            [
                {"video_id": "fb-1", "upload_url": "https://rupload.test/fb-1"},
                httpx.Response(200, json={"success": True}, request=httpx.Request("POST", "/")),
                {"success": True},
            ],
        )
        client = MetaClient(meta_target(), settings)

        video_id, upload_url = client.start_page_reel()
        client.upload_page_reel(upload_url, video_url="https://bucket.test/v.mp4")
        client.finish_page_reel(video_id, description="A dodo.")

        assert video_id == "fb-1"
        # The file is handed over as a link, in a header, exactly once.
        assert recorder.calls[1]["headers"]["file_url"] == "https://bucket.test/v.mp4"
        assert recorder.calls[2]["data"]["video_state"] == "PUBLISHED"

    def test_a_missing_permalink_is_not_fatal(self, settings, monkeypatch) -> None:
        store_meta_app(settings)
        _install(
            monkeypatch,
            [httpx.Response(400, json={"error": {"code": 100}},
                            request=httpx.Request("GET", "/"))],
        )
        client = MetaClient(meta_target(), settings)

        # The post already exists; not knowing its link must not undo that.
        assert client.media_permalink("media-9") == ""


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("code", "status", "expected"),
        [
            (190, 400, "meta_auth_required"),
            (200, 403, "meta_scope_missing"),
            (4, 400, "meta_rate_limited"),
            (2207026, 400, "meta_media_rejected"),
            (1, 500, "meta_api_failed"),
        ],
    )
    def test_each_kind_of_failure_gets_its_own_code(
        self, code: int, status: int, expected: str
    ) -> None:
        response = httpx.Response(
            status,
            json={"error": {"code": code, "message": "boom"}},
            request=httpx.Request("POST", "https://graph.facebook.com/"),
        )

        error = map_graph_error(response, stage="ig-publish")

        assert error.code.value == expected
        assert error.suggestion

    def test_a_token_shaped_string_is_scrubbed_from_the_details(self) -> None:
        leaked = "EAA" + "b" * 40
        response = httpx.Response(
            400,
            json={"error": {"code": 1, "message": f"bad token {leaked} here"}},
            request=httpx.Request("POST", "https://graph.facebook.com/"),
        )

        error = map_graph_error(response, stage="ig-publish")

        assert leaked not in (error.details or "")
        assert "[gizlendi]" in (error.details or "")


# --- the HTTP surface -------------------------------------------------------


class TestEndpoints:
    def test_status_is_reachable_and_carries_no_credential(self, client) -> None:
        response = client.get("/api/publishing/meta/status")

        assert response.status_code == 200
        body = response.json()
        assert body["appConfigured"] is False
        assert "appId" not in body
        assert "appSecret" not in body
        assert "accessToken" not in json.dumps(body)

    def test_the_callback_reports_a_refusal_as_a_page(self, client) -> None:
        response = client.get(
            "/api/publishing/meta/callback",
            params={"error": "access_denied", "error_description": "İzin verilmedi"},
        )

        assert response.status_code == 400
        assert "İzin verilmedi" in response.text
        assert "tamamlanmadı" in response.text

    def test_credentials_can_be_stored_over_http_and_never_read_back(
        self, client, settings
    ) -> None:
        response = client.post(
            "/api/publishing/meta/app-credentials",
            json={
                "appId": "1234567890123456",
                "appSecret": "fakemetaappsecretfortests0000",
                "replace": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["appConfigured"] is True
        assert "fakemetaappsecretfortests0000" not in response.text

        # There is no endpoint that returns it, on any router.
        again = client.get("/api/publishing/meta/status")
        assert "fakemetaappsecretfortests0000" not in again.text
