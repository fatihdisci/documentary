"""OAuth client discovery, scopes, token refresh, and what never leaves.

No real credential exists anywhere in this suite: the client and token files are
fixtures with the right *shape* and obviously fake values, and Google is never
contacted. What is under test is the app's own reasoning about them.
"""

from __future__ import annotations

import json

import pytest

from app.errors import AppError, EnvironmentError_, ValidationError
from app.publishing.youtube import (
    SCOPES,
    YouTubeCredentials,
    _safe_error_detail,
    map_api_error,
)
from tests.publishing_factories import FakeCredentials, write_client_file, write_token_file


# --- finding the client file ------------------------------------------------


def test_an_existing_client_file_is_detected_without_configuration(settings) -> None:
    path = write_client_file(settings)

    store = YouTubeCredentials(settings)

    assert store.client_file() == path
    assert store.available_client_files() == [path.name]
    assert store.status().client_file_present is True
    assert store.status().client_file_name == path.name


def test_the_newest_valid_client_file_wins_when_several_exist(settings) -> None:
    import os
    import time

    older = write_client_file(settings, name="client_secret_old.json")
    newer = write_client_file(settings, name="client_secret_new.json")
    # Make the ordering unambiguous regardless of filesystem timestamp resolution.
    os.utime(older, (time.time() - 3600, time.time() - 3600))

    assert YouTubeCredentials(settings).client_file() == newer


def test_a_configured_client_file_is_preferred(settings) -> None:
    chosen = write_client_file(settings, name="client_secret_chosen.json")
    write_client_file(settings, name="client_secret_other.json")
    settings.save_mutable(
        settings.mutable.model_copy(update={"youtube_client_secret_file": chosen.name})
    )

    assert YouTubeCredentials(settings).client_file() == chosen


def test_a_missing_client_file_is_a_clear_environment_error(settings) -> None:
    store = YouTubeCredentials(settings)

    with pytest.raises(EnvironmentError_) as excinfo:
        store.require_client_file()
    assert excinfo.value.code.value == "youtube_client_missing"
    assert "secrets" in (excinfo.value.details or "")

    status = store.status()
    assert status.client_file_present is False
    assert status.connected is False
    assert status.problem


def test_a_token_file_is_never_mistaken_for_a_client_file(settings) -> None:
    write_token_file(settings, scopes=list(SCOPES))

    assert YouTubeCredentials(settings).available_client_files() == []


# --- validating an uploaded client file -------------------------------------


def test_a_web_client_file_is_rejected_with_the_reason(settings) -> None:
    payload = json.dumps({"web": {"client_id": "x", "client_secret": "y"}}).encode()

    with pytest.raises(ValidationError) as excinfo:
        YouTubeCredentials.validate_client_payload(payload, "client_secret_web.json")
    assert excinfo.value.code.value == "youtube_client_invalid"
    assert "Desktop app" in excinfo.value.message


def test_malformed_json_is_rejected(settings) -> None:
    with pytest.raises(ValidationError):
        YouTubeCredentials.validate_client_payload(b"{not json", "client_secret.json")


def test_a_client_file_missing_required_fields_is_rejected(settings) -> None:
    payload = json.dumps({"installed": {"client_id": "only-an-id"}}).encode()

    with pytest.raises(ValidationError) as excinfo:
        YouTubeCredentials.validate_client_payload(payload, "client_secret.json")
    assert "client_secret" in (excinfo.value.message + (excinfo.value.details or ""))


def test_an_invalid_client_file_is_not_written_to_disk(settings) -> None:
    store = YouTubeCredentials(settings)

    with pytest.raises(ValidationError):
        store.store_client_file(b"{}", "client_secret.json")

    assert store.available_client_files() == []


def test_a_valid_client_file_is_stored_owner_only(settings) -> None:
    valid = json.dumps(
        {
            "installed": {
                "client_id": "000000000000-test.apps.googleusercontent.com",
                "client_secret": "fake-client-secret-for-tests",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
    ).encode()

    name = YouTubeCredentials(settings).store_client_file(valid, "../../evil name.json")

    stored = settings.oauth_secrets_dir / name
    assert "/" not in name and ".." not in name
    assert stored.is_file()
    assert stored.stat().st_mode & 0o777 == 0o600


# --- scopes and refresh -----------------------------------------------------


def test_a_narrow_token_reports_the_missing_caption_scope(settings) -> None:
    write_client_file(settings)
    write_token_file(
        settings,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
    )

    store = YouTubeCredentials(settings)
    status = store.status()

    assert status.token_present is True
    assert status.scopes_sufficient is False
    assert status.needs_reconnect is True
    assert status.missing_scopes == ["https://www.googleapis.com/auth/youtube.force-ssl"]
    assert "yeniden bağlayın" in (status.suggestion or "")

    with pytest.raises(AppError) as excinfo:
        store.usable_credentials()
    assert excinfo.value.code.value == "youtube_scope_missing"


def test_a_full_scope_token_is_usable(settings) -> None:
    write_client_file(settings)
    write_token_file(settings, scopes=list(SCOPES))

    status = YouTubeCredentials(settings).status()

    assert status.scopes_sufficient is True
    assert status.missing_scopes == []


def test_an_expired_token_is_refreshed_and_written_back(settings, monkeypatch) -> None:
    write_client_file(settings)
    token_file = write_token_file(settings, scopes=list(SCOPES))
    store = YouTubeCredentials(settings)

    refreshed = FakeCredentials(valid=False)
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        lambda *_args, **_kwargs: refreshed,
    )

    credentials = store.load_credentials()

    assert refreshed.refreshed is True
    assert credentials is refreshed
    # The refreshed grant is persisted, so the next start does not refresh again.
    assert json.loads(token_file.read_text("utf-8"))["scopes"] == list(SCOPES)
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_a_revoked_grant_does_not_delete_the_token_behind_the_users_back(
    settings, monkeypatch
) -> None:
    from google.auth.exceptions import RefreshError

    write_client_file(settings)
    token_file = write_token_file(settings, scopes=list(SCOPES))

    class Revoked(FakeCredentials):
        def refresh(self, _request):  # noqa: ANN001, ANN202
            raise RefreshError("revoked")

    revoked = Revoked(valid=False)
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        lambda *_args, **_kwargs: revoked,
    )

    status = YouTubeCredentials(settings).status()

    assert token_file.is_file()
    assert status.needs_reconnect is True
    assert status.connected is False


def test_disconnect_removes_only_the_token(settings) -> None:
    client = write_client_file(settings)
    token = write_token_file(settings, scopes=list(SCOPES))

    status = YouTubeCredentials(settings).disconnect()

    assert not token.exists()
    assert client.is_file()
    assert status.client_file_present is True
    assert status.token_present is False


def test_no_credential_value_appears_in_the_connection_report(settings) -> None:
    write_client_file(settings)
    write_token_file(settings, scopes=list(SCOPES))

    payload = YouTubeCredentials(settings).status().model_dump_json()

    for secret in (
        "fake-client-secret-for-tests",
        "fake-refresh-token-for-tests",
        "fake-access-token-for-tests",
        "000000000000-testclient",
    ):
        assert secret not in payload


def test_error_details_are_scrubbed_of_anything_credential_shaped() -> None:
    text = (
        'client_secret: "GOCSPX-superSecretValue"\n'
        "Authorization: Bearer ya29.averyLongAccessTokenValue\n"
        "refresh_token=1//0gSecretRefreshValue"
    )

    cleaned = _safe_error_detail(text)

    assert "GOCSPX-superSecretValue" not in cleaned
    assert "ya29.averyLongAccessTokenValue" not in cleaned
    assert "0gSecretRefreshValue" not in cleaned


# --- error mapping ----------------------------------------------------------


def _http_error(status: int, reason: str, message: str = "nope"):  # noqa: ANN202
    from googleapiclient.errors import HttpError

    class Response:
        def __init__(self, code: int) -> None:
            self.status = code
            self.reason = "error"

    body = json.dumps(
        {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    ).encode()
    return HttpError(Response(status), body)


def test_quota_permission_and_metadata_errors_get_their_own_codes() -> None:
    assert (
        map_api_error(_http_error(403, "quotaExceeded"), stage="upload").code.value
        == "youtube_quota_exceeded"
    )
    assert (
        map_api_error(_http_error(401, "authError"), stage="upload").code.value
        == "youtube_auth_required"
    )
    assert (
        map_api_error(_http_error(403, "insufficientPermissions"), stage="caption").code.value
        == "youtube_scope_missing"
    )
    assert (
        map_api_error(_http_error(400, "invalidTitle"), stage="upload").code.value
        == "youtube_invalid_metadata"
    )
    assert (
        map_api_error(OSError("connection reset"), stage="upload").code.value
        == "youtube_network_failed"
    )
    assert (
        map_api_error(_http_error(500, "backendError"), stage="thumbnail").code.value
        == "youtube_thumbnail_failed"
    )
    assert (
        map_api_error(_http_error(500, "backendError"), stage="caption").code.value
        == "youtube_caption_failed"
    )
