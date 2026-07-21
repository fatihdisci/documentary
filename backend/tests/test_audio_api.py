"""Audio API: narration generation, import, timing and subtitle export."""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_ffmpeg
from tests.factories import load_dodo_package, make_wav_bytes

#: Real network calls are opt-in so the suite stays fast and offline-safe.
network = pytest.mark.skipif(
    not os.environ.get("EVB_TEST_NETWORK"),
    reason="set EVB_TEST_NETWORK=1 to exercise real Edge TTS",
)


def make_project(client: TestClient, scenes: int = 3) -> str:
    slug = client.post("/api/projects", json={"name": "Dodo"}).json()["project"]["slug"]
    package = load_dodo_package()
    package["scenes"] = package["scenes"][:scenes]
    client.post(f"/api/projects/{slug}/content", json={"content": package})
    return slug


def attach_audio(client: TestClient, slug: str, unit_id: str, seconds: float) -> dict:
    response = client.post(
        f"/api/projects/{slug}/audio/import/{unit_id}",
        files=[("file", (f"{unit_id}.wav", io.BytesIO(make_wav_bytes(seconds)), "audio/wav"))],
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestProviders:
    def test_lists_every_provider_with_status(self, client: TestClient) -> None:
        providers = {p["name"]: p for p in client.get("/api/tts/providers").json()["providers"]}
        assert set(providers) == {"edge", "imported", "elevenlabs"}
        assert providers["imported"]["available"] is True
        assert providers["imported"]["offline"] is True

    def test_elevenlabs_reports_the_missing_key_without_failing(self, client: TestClient) -> None:
        providers = {p["name"]: p for p in client.get("/api/tts/providers").json()["providers"]}
        assert providers["elevenlabs"]["available"] is False
        assert providers["elevenlabs"]["requiresApiKey"] is True

    def test_unknown_provider_voices_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/tts/voices?provider=bogus")
        assert response.status_code == 422
        assert response.json()["code"] == "tts_provider_unavailable"

    @network
    def test_real_edge_voice_list(self, client: TestClient) -> None:
        voices = client.get("/api/tts/voices?provider=edge").json()
        assert len(voices) > 50
        assert any(v["id"] == "en-US-GuyNeural" for v in voices)


@requires_ffmpeg
class TestImportedAudio:
    def test_upload_measures_the_real_duration(self, client: TestClient) -> None:
        slug = make_project(client, scenes=1)
        scene_id = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]["id"]

        body = attach_audio(client, slug, scene_id, 3.5)
        result = body["results"][0]

        assert result["durationSeconds"] == pytest.approx(3.5, abs=0.05)
        assert result["audioFile"].startswith("audio/imported/")
        assert body["project"]["scenes"][0]["audioSource"] == "imported"

    def test_uploaded_audio_is_playable_over_http(self, client: TestClient) -> None:
        slug = make_project(client, scenes=1)
        scene_id = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]["id"]
        url = attach_audio(client, slug, scene_id, 2.0)["results"][0]["audioUrl"]

        response = client.get(url)
        assert response.status_code == 200
        assert len(response.content) > 1000

    def test_rejects_an_unsupported_audio_type(self, client: TestClient) -> None:
        slug = make_project(client, scenes=1)
        scene_id = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]["id"]

        response = client.post(
            f"/api/projects/{slug}/audio/import/{scene_id}",
            files=[("file", ("voice.aiff", io.BytesIO(b"x" * 100), "audio/aiff"))],
        )
        assert response.status_code == 422
        assert response.json()["code"] == "unsupported_audio"

    def test_rejects_a_file_that_is_not_audio(self, client: TestClient) -> None:
        slug = make_project(client, scenes=1)
        scene_id = client.get(f"/api/projects/{slug}").json()["project"]["scenes"][0]["id"]

        response = client.post(
            f"/api/projects/{slug}/audio/import/{scene_id}",
            files=[("file", ("fake.wav", io.BytesIO(b"not actually a wav"), "audio/wav"))],
        )
        assert response.status_code in {400, 422}
        assert response.json()["code"] in {"corrupt_audio", "unsupported_audio"}


@requires_ffmpeg
class TestOfflineEndToEnd:
    """The complete workflow with no online TTS provider involved at all."""

    def test_import_audio_then_get_timing_and_subtitles(self, client: TestClient) -> None:
        slug = make_project(client, scenes=3)
        project = client.get(f"/api/projects/{slug}").json()["project"]

        durations = [6.0, 9.0, 4.5]
        for scene, seconds in zip(project["scenes"], durations, strict=True):
            attach_audio(client, slug, scene["id"], seconds)
        attach_audio(client, slug, "intro", 5.0)
        attach_audio(client, slug, "outro", 7.0)

        timing = client.get(f"/api/projects/{slug}/audio/timing").json()

        assert len(timing["entries"]) == 5
        assert [e["kind"] for e in timing["entries"]] == ["intro", "scene", "scene", "scene", "outro"]
        assert timing["summary"]["totalSeconds"] > sum(durations)
        assert timing["cueCount"] > 3

        # Scene durations follow their own measured audio, not a fixed value.
        scene_entries = [e for e in timing["entries"] if e["kind"] == "scene"]
        lengths = [e["durationSeconds"] for e in scene_entries]
        assert len(set(round(v, 2) for v in lengths)) == 3, f"durations should differ: {lengths}"

        srt = client.get(f"/api/projects/{slug}/audio/subtitles.srt").text
        assert "-->" in srt
        assert srt.startswith("1\n")

    def test_scene_srt_is_exported_separately(self, client: TestClient) -> None:
        slug = make_project(client, scenes=2)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        scene_id = project["scenes"][0]["id"]
        attach_audio(client, slug, scene_id, 6.0)

        response = client.get(f"/api/projects/{slug}/audio/subtitles/{scene_id}.srt")
        assert response.status_code == 200
        assert "-->" in response.text

    def test_subtitles_without_audio_explain_what_is_needed(self, client: TestClient) -> None:
        slug = make_project(client, scenes=2)
        response = client.get(f"/api/projects/{slug}/audio/subtitles.srt")
        assert response.status_code == 422
        body = response.json()
        assert "narration audio" in body["suggestion"]

    def test_generating_with_the_imported_provider_says_what_to_upload(
        self, client: TestClient
    ) -> None:
        slug = make_project(client, scenes=1)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        project["audio"]["ttsProvider"] = "imported"
        client.put(f"/api/projects/{slug}", json=project)

        response = client.post(f"/api/projects/{slug}/audio/generate", json={})
        assert response.status_code == 400
        assert response.json()["code"] == "missing_audio"
        assert "upload" in response.json()["suggestion"].lower()


@requires_ffmpeg
class TestTiming:
    def test_timing_reflects_the_configured_duration_mode(self, client: TestClient) -> None:
        slug = make_project(client, scenes=3)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        for scene in project["scenes"]:
            attach_audio(client, slug, scene["id"], 8.0)

        audio_mode = client.get(f"/api/projects/{slug}/audio/timing").json()

        project = client.get(f"/api/projects/{slug}").json()["project"]
        project["video"]["durationMode"] = "target"
        project["video"]["targetDurationSeconds"] = 240.0
        client.put(f"/api/projects/{slug}", json=project)

        target_mode = client.get(f"/api/projects/{slug}/audio/timing").json()

        assert target_mode["summary"]["totalSeconds"] > audio_mode["summary"]["totalSeconds"]
        assert target_mode["summary"]["durationMode"] == "target"

    def test_timing_endpoint_does_not_fail_on_an_incomplete_project(
        self, client: TestClient
    ) -> None:
        """Users need to see timing while still filling scenes in."""
        slug = make_project(client, scenes=3)
        response = client.get(f"/api/projects/{slug}/audio/timing")
        assert response.status_code == 200


@requires_ffmpeg
@network
class TestRealEdgeTTS:
    """Exercised against the live service when EVB_TEST_NETWORK=1."""

    def test_generates_narration_and_measures_it(self, client: TestClient) -> None:
        slug = make_project(client, scenes=2)

        response = client.post(f"/api/projects/{slug}/audio/generate", json={})
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["generatedCount"] >= 2
        for result in body["results"]:
            assert result["durationSeconds"] > 0.5

        # Real narration produces real, differing scene durations.
        timing = client.get(f"/api/projects/{slug}/audio/timing").json()
        lengths = [e["durationSeconds"] for e in timing["entries"]]
        assert len(set(round(v, 2) for v in lengths)) > 1

    def test_second_run_reuses_cached_audio(self, client: TestClient) -> None:
        slug = make_project(client, scenes=2)
        client.post(f"/api/projects/{slug}/audio/generate", json={})

        again = client.post(f"/api/projects/{slug}/audio/generate", json={}).json()
        assert again["generatedCount"] == 0, "unchanged narration must not be re-synthesized"
