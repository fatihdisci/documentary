"""Project API: the full M2 user workflow through HTTP."""

from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from tests.factories import load_dodo_package, make_image_bytes


def create_project(client: TestClient, name: str = "The Dodo") -> str:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["project"]["slug"]


def upload_images(client: TestClient, slug: str, count: int = 10) -> dict:
    names = [
        "opening", "habitat", "anatomy", "diet", "arrival",
        "predators", "forest", "last-sighting", "bones", "conservation",
    ]
    files = [
        (
            "files",
            (
                f"{i + 1:02d}-{names[i] if i < len(names) else f'scene{i}'}.png",
                io.BytesIO(make_image_bytes(seed=i)),
                "image/png",
            ),
        )
        for i in range(count)
    ]
    response = client.post(f"/api/projects/{slug}/images", files=files)
    assert response.status_code == 201, response.text
    return response.json()


class TestLifecycle:
    def test_create_list_get(self, client: TestClient) -> None:
        slug = create_project(client)
        assert slug == "the-dodo"

        listed = client.get("/api/projects").json()
        assert [p["slug"] for p in listed] == ["the-dodo"]

        fetched = client.get(f"/api/projects/{slug}").json()
        assert fetched["project"]["name"] == "The Dodo"
        assert fetched["images"] == []

    def test_new_project_inherits_configured_defaults(self, client: TestClient) -> None:
        settings = client.get("/api/settings").json()["settings"]
        settings["defaultVoice"] = "en-GB-RyanNeural"
        settings["defaultQuality"] = "high"
        client.put("/api/settings", json=settings)

        project = client.post("/api/projects", json={"name": "Thylacine"}).json()["project"]
        assert project["audio"]["voice"] == "en-GB-RyanNeural"
        assert project["export"]["quality"] == "high"

    def test_missing_project_returns_a_helpful_404(self, client: TestClient) -> None:
        response = client.get("/api/projects/nope")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "project_not_found"
        assert "nope" in body["message"]
        assert body["suggestion"]

    def test_update_preserves_identity(self, client: TestClient) -> None:
        slug = create_project(client)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        original_id = project["projectId"]

        project["projectId"] = "hacked"
        project["slug"] = "hacked"
        project["metadata"]["videoTitle"] = "Updated"

        saved = client.put(f"/api/projects/{slug}", json=project).json()["project"]
        assert saved["projectId"] == original_id
        assert saved["slug"] == slug
        assert saved["metadata"]["videoTitle"] == "Updated"

    def test_rename_keeps_the_folder_slug(self, client: TestClient) -> None:
        slug = create_project(client)
        renamed = client.post(f"/api/projects/{slug}/rename", json={"name": "Dodo Redux"}).json()
        assert renamed["project"]["name"] == "Dodo Redux"
        assert renamed["project"]["slug"] == slug

    def test_delete_requires_confirmation(self, client: TestClient) -> None:
        slug = create_project(client)

        unconfirmed = client.delete(f"/api/projects/{slug}")
        assert unconfirmed.status_code == 409
        assert "confirm" in unconfirmed.json()["suggestion"]
        assert client.get(f"/api/projects/{slug}").status_code == 200, "must not have deleted"

        confirmed = client.delete(f"/api/projects/{slug}?confirm={slug}")
        assert confirmed.status_code == 204
        assert client.get(f"/api/projects/{slug}").status_code == 404

    def test_archive_round_trip(self, client: TestClient) -> None:
        slug = create_project(client)
        assert client.post(f"/api/projects/{slug}/archive").status_code == 204
        assert client.get("/api/projects?include_archived=false").json() == []
        assert client.get("/api/projects").json()[0]["archived"] is True
        assert client.post(f"/api/projects/{slug}/unarchive").status_code == 200

    def test_duplicate_creates_an_independent_copy(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 3)
        copy = client.post(f"/api/projects/{slug}/duplicate", json={"name": "Dodo Copy"}).json()

        assert copy["project"]["slug"] == "dodo-copy"
        images = client.get(f"/api/projects/dodo-copy/images").json()
        assert len(images) == 3
        # Editing the copy leaves the original alone.
        assert client.get(f"/api/projects/{slug}").json()["project"]["name"] == "The Dodo"


class TestImages:
    def test_upload_validate_and_list(self, client: TestClient) -> None:
        slug = create_project(client)
        result = upload_images(client, slug, 10)

        assert len(result["images"]) == 10
        assert result["images"][0]["filename"] == "01-opening.png"
        assert result["images"][0]["width"] == 1920
        assert result["images"][0]["thumbnailUrl"]

        listed = client.get(f"/api/projects/{slug}/images").json()
        assert len(listed) == 10

    def test_thumbnails_are_served(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 1)
        response = client.get(f"/api/projects/{slug}/media/thumbnails/01-opening.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_original_image_is_served(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 1)
        response = client.get(f"/api/projects/{slug}/media/images/01-opening.png")
        assert response.status_code == 200
        assert response.content == make_image_bytes(seed=0)

    def test_media_path_traversal_is_blocked(self, client: TestClient) -> None:
        slug = create_project(client)
        response = client.get(f"/api/projects/{slug}/media/images/..%2F..%2Fproject.json")
        assert response.status_code in {400, 404}
        assert b"schemaVersion" not in response.content

    def test_rejects_a_corrupt_upload_with_a_reason(self, client: TestClient) -> None:
        slug = create_project(client)
        response = client.post(
            f"/api/projects/{slug}/images",
            files=[("files", ("broken.png", io.BytesIO(b"not an image"), "image/png"))],
        )
        assert response.status_code == 422
        assert response.json()["code"] == "corrupt_image"

    def test_deleting_an_image_detaches_it_from_scenes(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 10)
        client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})

        assert client.delete(f"/api/projects/{slug}/images/01-opening.png").status_code == 204

        project = client.get(f"/api/projects/{slug}").json()["project"]
        assert project["scenes"][0]["imageFile"] is None
        assert len(client.get(f"/api/projects/{slug}/images").json()) == 9


class TestContentImport:
    def test_example_template_is_downloadable_and_valid(self, client: TestClient) -> None:
        response = client.get("/api/projects/content/example")
        assert response.status_code == 200
        package = response.json()
        assert package["commonName"] == "Dodo"
        assert len(package["scenes"]) == 10

    def test_import_populates_and_maps_images(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 10)

        response = client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})
        assert response.status_code == 200
        body = response.json()

        assert body["report"]["scenesCreated"] == 10
        assert body["report"]["imagesMapped"] == 10
        project = body["project"]
        assert project["animal"]["scientificName"] == "Raphus cucullatus"
        assert project["scenes"][0]["imageFile"] == "01-opening.png"
        assert project["scenes"][0]["narration"]

    def test_import_survives_a_reload(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 10)
        client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})

        reloaded = client.get(f"/api/projects/{slug}").json()["project"]
        assert len(reloaded["scenes"]) == 10
        assert reloaded["metadata"]["thumbnailText"] == "GONE IN 100 YEARS"

    def test_file_upload_import_keeps_the_original(self, client: TestClient) -> None:
        slug = create_project(client)
        payload = json.dumps(load_dodo_package()).encode()
        response = client.post(
            f"/api/projects/{slug}/content/upload",
            files=[("file", ("dodo.json", io.BytesIO(payload), "application/json"))],
        )
        assert response.status_code == 200
        assert response.json()["report"]["scenesCreated"] == 10

    def test_invalid_json_reports_the_position(self, client: TestClient) -> None:
        slug = create_project(client)
        response = client.post(
            f"/api/projects/{slug}/content/upload",
            files=[("file", ("bad.json", io.BytesIO(b'{"scenes": [,]}'), "application/json"))],
        )
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "invalid_json"
        assert "line 1" in body["message"]

    def test_content_export_round_trips(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 10)
        client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})

        exported = client.get(f"/api/projects/{slug}/content/export").json()
        assert exported["commonName"] == "Dodo"
        assert len(exported["scenes"]) == 10
        assert exported["scenes"][3]["focusX"] == 0.38

        # Re-importing the export into a fresh project reproduces it.
        other = create_project(client, "Dodo Two")
        result = client.post(f"/api/projects/{other}/content", json={"content": exported}).json()
        assert result["report"]["scenesCreated"] == 10
        assert result["project"]["metadata"]["thumbnailText"] == "GONE IN 100 YEARS"


class TestScenes:
    def _project_with_scenes(self, client: TestClient) -> str:
        slug = create_project(client)
        upload_images(client, slug, 10)
        client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})
        return slug

    def test_reorder(self, client: TestClient) -> None:
        slug = self._project_with_scenes(client)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        ids = [s["id"] for s in project["scenes"]]
        reversed_ids = list(reversed(ids))

        response = client.post(f"/api/projects/{slug}/scenes/reorder", json={"sceneIds": reversed_ids})
        assert response.status_code == 200
        scenes = response.json()["project"]["scenes"]
        assert [s["id"] for s in scenes] == reversed_ids
        assert [s["order"] for s in scenes] == list(range(10))

    def test_reorder_rejects_an_incomplete_list(self, client: TestClient) -> None:
        slug = self._project_with_scenes(client)
        ids = [s["id"] for s in client.get(f"/api/projects/{slug}").json()["project"]["scenes"]]

        response = client.post(f"/api/projects/{slug}/scenes/reorder", json={"sceneIds": ids[:5]})
        assert response.status_code == 422
        assert "every scene exactly once" in response.json()["message"]
        # The real order is untouched.
        after = client.get(f"/api/projects/{slug}").json()["project"]["scenes"]
        assert [s["id"] for s in after] == ids

    def test_add_update_and_delete(self, client: TestClient) -> None:
        slug = create_project(client)
        added = client.post(f"/api/projects/{slug}/scenes").json()["project"]
        assert len(added["scenes"]) == 1

        scene = added["scenes"][0]
        scene["title"] = "New Scene"
        scene["narration"] = "Some narration."
        updated = client.put(f"/api/projects/{slug}/scenes/{scene['id']}", json=scene).json()
        assert updated["project"]["scenes"][0]["title"] == "New Scene"

        deleted = client.delete(f"/api/projects/{slug}/scenes/{scene['id']}").json()
        assert deleted["project"]["scenes"] == []

    def test_duplicate_scene_does_not_share_audio(self, client: TestClient) -> None:
        slug = self._project_with_scenes(client)
        scenes = client.get(f"/api/projects/{slug}").json()["project"]["scenes"]
        source = scenes[0]

        result = client.post(f"/api/projects/{slug}/scenes/{source['id']}/duplicate").json()
        copy = result["project"]["scenes"][1]

        assert len(result["project"]["scenes"]) == 11
        assert copy["title"] == source["title"]
        assert copy["id"] != source["id"]
        assert copy["audioFile"] is None

    def test_invalid_scene_edit_is_rejected_with_field_detail(self, client: TestClient) -> None:
        slug = self._project_with_scenes(client)
        scene = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]
        # A pan with no zoom headroom would expose black borders.
        scene["startScale"] = 1.0
        scene["endScale"] = 1.0
        scene["startX"] = 0.1
        scene["endX"] = 0.9

        response = client.put(f"/api/projects/{slug}/scenes/{scene['id']}", json=scene)
        assert response.status_code == 422

    def test_assign_image_rejects_a_missing_file(self, client: TestClient) -> None:
        slug = self._project_with_scenes(client)
        scene_id = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]["id"]
        response = client.post(
            f"/api/projects/{slug}/scenes/{scene_id}/image", json={"imageFile": "ghost.png"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "missing_image"


class TestBundles:
    def test_export_and_reimport_through_the_api(self, client: TestClient) -> None:
        slug = create_project(client)
        upload_images(client, slug, 3)
        client.post(f"/api/projects/{slug}/content", json={"content": load_dodo_package()})

        bundle = client.get(f"/api/projects/{slug}/bundle")
        assert bundle.status_code == 200
        assert bundle.content[:2] == b"PK"

        imported = client.post(
            "/api/projects/import-bundle",
            files=[("file", ("dodo.zip", io.BytesIO(bundle.content), "application/zip"))],
        )
        assert imported.status_code == 201
        new_slug = imported.json()["project"]["slug"]
        assert new_slug != slug
        assert len(client.get(f"/api/projects/{new_slug}/images").json()) == 3
