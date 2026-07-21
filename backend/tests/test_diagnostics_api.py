"""Diagnostics, settings and error-handling endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import requires_ffmpeg


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


@requires_ffmpeg
def test_diagnostics_reports_real_ffmpeg_facts(client: TestClient) -> None:
    body = client.get("/api/diagnostics").json()
    checks = {c["id"]: c for c in body["checks"]}

    assert body["healthy"] is True
    assert checks["ffmpeg"]["status"] == "ok"
    assert "ffmpeg version" in checks["ffmpeg"]["value"]
    assert checks["filters"]["status"] == "ok"
    assert checks["encoders"]["status"] == "ok"


@requires_ffmpeg
def test_diagnostics_explains_the_text_engine(client: TestClient) -> None:
    """The app must state which text engine it uses and why, not stay silent."""
    checks = {c["id"]: c for c in client.get("/api/diagnostics").json()["checks"]}
    text = checks["text-engine"]
    assert text["status"] == "ok"
    assert "Pillow" in text["value"]
    # It reports the drawtext situation either way.
    assert "drawtext" in text["detail"]


def test_diagnostics_uses_camel_case(client: TestClient) -> None:
    body = client.get("/api/diagnostics").json()
    assert "generatedAt" in body
    assert "healthy" in body


def test_narration_source_check_always_offers_import(client: TestClient) -> None:
    """Even with no network, the app must never claim narration is impossible."""
    checks = {c["id"]: c for c in client.get("/api/diagnostics").json()["checks"]}
    assert "imported" in checks["tts"]["value"]


class TestSettings:
    def test_read_returns_camel_case_and_no_secret_values(self, client: TestClient) -> None:
        body = client.get("/api/settings").json()
        assert "ffmpegPath" in body["settings"]
        assert body["configuredSecrets"] == []
        assert "resolvedPaths" in body

    def test_update_round_trip(self, client: TestClient) -> None:
        current = client.get("/api/settings").json()["settings"]
        current["defaultVoice"] = "en-GB-RyanNeural"
        updated = client.put("/api/settings", json=current)
        assert updated.status_code == 200
        assert updated.json()["settings"]["defaultVoice"] == "en-GB-RyanNeural"
        # Persisted, not just echoed back.
        assert client.get("/api/settings").json()["settings"]["defaultVoice"] == "en-GB-RyanNeural"

    def test_bad_ffmpeg_path_is_rejected_with_guidance(self, client: TestClient) -> None:
        current = client.get("/api/settings").json()["settings"]
        current["ffmpegPath"] = "/definitely/not/here/ffmpeg"
        response = client.put("/api/settings", json=current)
        body = response.json()
        assert response.status_code == 422
        assert body["code"] == "ffmpeg_not_found"
        assert "/definitely/not/here/ffmpeg" in body["message"]
        assert body["suggestion"]  # never empty

    def test_secret_values_are_write_only(self, client: TestClient, isolated_data_dir: Path) -> None:
        response = client.post(
            "/api/settings/secrets", json={"key": "elevenlabs_api_key", "value": "super-secret-value"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["configuredSecrets"] == ["elevenlabs_api_key"]
        # The value appears nowhere in any response.
        assert "super-secret-value" not in response.text
        assert "super-secret-value" not in client.get("/api/settings").text

    def test_secret_file_is_not_world_readable(self, client: TestClient, isolated_data_dir: Path) -> None:
        client.post("/api/settings/secrets", json={"key": "elevenlabs_api_key", "value": "k"})
        mode = (isolated_data_dir / "secrets.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_clearing_a_secret(self, client: TestClient) -> None:
        client.post("/api/settings/secrets", json={"key": "elevenlabs_api_key", "value": "k"})
        body = client.post("/api/settings/secrets", json={"key": "elevenlabs_api_key", "value": None}).json()
        assert body["configuredSecrets"] == []

    def test_unknown_secret_rejected(self, client: TestClient) -> None:
        response = client.post("/api/settings/secrets", json={"key": "aws_root_key", "value": "x"})
        assert response.status_code == 422
        assert response.json()["code"] == "schema_validation"


class TestErrorPayloads:
    def test_every_error_carries_message_suggestion_and_log_path(self, client: TestClient) -> None:
        current = client.get("/api/settings").json()["settings"]
        current["ffmpegPath"] = "/nope/ffmpeg"
        body = client.put("/api/settings", json=current).json()
        for field in ("code", "message", "suggestion", "logPath"):
            assert body.get(field), f"error payload is missing '{field}'"
        # The message must be specific, never a shrug.
        assert body["message"].lower() != "something went wrong"
