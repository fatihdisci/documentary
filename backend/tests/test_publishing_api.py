"""The publishing HTTP surface.

The point of this file is the shape of the contract: typed responses, camelCase
on the wire, structured errors, and — the one that matters most — that no
response on any of these routes can carry a credential.
"""

from __future__ import annotations

import json

from app.publishing.youtube import SCOPES
from tests.publishing_factories import (
    PNG_BYTES,
    SRT_TEXT,
    add_long_render,
    add_short,
    make_project,
    write_client_file,
    write_token_file,
)


# --- connection -------------------------------------------------------------


def test_status_reports_no_connection_on_a_fresh_install(client) -> None:
    response = client.get("/api/publishing/youtube/status")

    assert response.status_code == 200
    body = response.json()
    assert body["clientFilePresent"] is False
    assert body["connected"] is False
    assert body["statusMessage"]


def test_status_never_contains_a_credential(client, settings) -> None:
    write_client_file(settings)
    write_token_file(settings, scopes=list(SCOPES))

    raw = client.get("/api/publishing/youtube/status").text

    for secret in (
        "fake-client-secret-for-tests",
        "fake-refresh-token-for-tests",
        "fake-access-token-for-tests",
    ):
        assert secret not in raw
    # Not even the full path to the file is exposed; only its basename.
    assert str(settings.oauth_secrets_dir) not in raw


def test_uploading_an_invalid_client_file_is_a_structured_error(client) -> None:
    response = client.post(
        "/api/publishing/youtube/client-secret",
        files={"file": ("client_secret.json", b'{"web": {}}', "application/json")},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "youtube_client_invalid"
    assert body["suggestion"]
    assert body["message"]


def test_uploading_a_valid_client_file_installs_and_selects_it(client, settings) -> None:
    payload = json.dumps(
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

    response = client.post(
        "/api/publishing/youtube/client-secret",
        files={"file": ("client_secret_test.json", payload, "application/json")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["connection"]["clientFilePresent"] is True
    assert body["storedFileName"] == body["connection"]["clientFileName"]
    assert "fake-client-secret-for-tests" not in response.text
    # The choice is remembered for next time.
    settings.reload_mutable()
    assert settings.mutable.youtube_client_secret_file == body["storedFileName"]


def test_disconnect_removes_the_token_but_keeps_the_client_file(client, settings) -> None:
    client_file = write_client_file(settings)
    token = write_token_file(settings, scopes=list(SCOPES))

    response = client.delete("/api/publishing/youtube/disconnect")

    assert response.status_code == 200
    assert response.json()["tokenPresent"] is False
    assert not token.exists()
    assert client_file.is_file()


# --- media and drafts -------------------------------------------------------


def test_media_endpoint_lists_long_videos_and_shorts(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    add_short(paths, slug=project.slug)

    response = client.get(f"/api/projects/{project.slug}/publishing/media")

    assert response.status_code == 200
    items = response.json()
    assert {item["mediaId"] for item in items} == {"long:render0001", "short:short00000001"}
    # camelCase on the wire, and no absolute paths anywhere.
    assert "sizeBytes" in items[0]
    assert str(paths.root) not in response.text


def test_draft_round_trips_through_the_api(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    url = f"/api/projects/{project.slug}/publishing/drafts/long:render0001"

    seeded = client.get(url).json()
    assert seeded["draft"]["youtube"]["title"] == project.metadata.video_title

    draft = seeded["draft"]
    draft["youtube"]["title"] = "Edited by hand"
    draft["youtube"]["tags"] = ["dodo", "dodo", " "]
    saved = client.put(url, json=draft)

    assert saved.status_code == 200
    assert saved.json()["draft"]["youtube"]["title"] == "Edited by hand"
    assert saved.json()["draft"]["youtube"]["tags"] == ["dodo"]
    assert client.get(url).json()["draft"]["youtube"]["title"] == "Edited by hand"


def test_a_draft_for_an_unknown_media_id_is_a_404(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)

    response = client.get(
        f"/api/projects/{project.slug}/publishing/drafts/long:does-not-exist"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "publishing_media_not_found"


def test_a_traversal_media_id_is_refused(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)

    response = client.get(
        f"/api/projects/{project.slug}/publishing/drafts/long:..%2F..%2Fsecrets"
    )

    assert response.status_code in {400, 404}
    assert response.json()["code"] in {"path_traversal", "publishing_media_not_found"}


def test_title_over_the_limit_is_rejected_by_the_api(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)
    url = f"/api/projects/{project.slug}/publishing/drafts/long:render0001"

    draft = client.get(url).json()["draft"]
    draft["youtube"]["title"] = "x" * 120

    response = client.put(url, json=draft)

    assert response.status_code == 422
    assert response.json()["code"] == "youtube_invalid_metadata"


# --- assets -----------------------------------------------------------------


def test_thumbnail_upload_and_download(client, settings) -> None:
    project, _ = make_project(settings)
    base = f"/api/projects/{project.slug}/publishing/assets/thumbnail"

    upload = client.post(base, files={"file": ("cover.png", PNG_BYTES, "image/png")})
    assert upload.status_code == 201
    name = upload.json()["filename"]

    served = client.get(f"{base}/{name}")
    assert served.status_code == 200
    assert served.content == PNG_BYTES


def test_a_non_image_thumbnail_is_refused(client, settings) -> None:
    project, _ = make_project(settings)

    response = client.post(
        f"/api/projects/{project.slug}/publishing/assets/thumbnail",
        files={"file": ("cover.png", b"not an image at all", "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "publishing_asset_invalid"


def test_caption_upload_and_an_empty_one_being_refused(client, settings) -> None:
    project, _ = make_project(settings)
    base = f"/api/projects/{project.slug}/publishing/assets/caption"

    good = client.post(base, files={"file": ("english.srt", SRT_TEXT.encode(), "text/plain")})
    assert good.status_code == 201

    empty = client.post(base, files={"file": ("english.srt", b"", "text/plain")})
    assert empty.status_code == 422
    assert empty.json()["code"] == "publishing_asset_invalid"


# --- history and jobs -------------------------------------------------------


def test_history_starts_empty_and_is_typed(client, settings) -> None:
    project, _ = make_project(settings)

    response = client.get(f"/api/projects/{project.slug}/publishing/history")

    assert response.status_code == 200
    assert response.json() == []


def test_an_unknown_job_is_a_structured_404(client) -> None:
    response = client.get("/api/publishing/jobs/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "publishing_job_not_found"
    assert body["suggestion"]


def test_publishing_without_a_draft_is_refused(client, settings) -> None:
    project, paths = make_project(settings)
    add_long_render(paths, slug=project.slug)

    response = client.post(
        f"/api/projects/{project.slug}/publishing/youtube",
        json={"mediaId": "long:render0001", "allowDuplicate": False},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "publishing_media_not_found"
