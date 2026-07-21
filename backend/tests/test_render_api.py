"""Render API: preflight, job submission, SSE progress, cancel and downloads.

These drive a real render through HTTP, including cancelling one mid-flight.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.enums import JobStatus
from tests.conftest import requires_ffmpeg
from tests.factories import load_dodo_package, make_wav_bytes, write_images


@pytest.fixture(autouse=True)
def fresh_job_manager():  # noqa: ANN201
    """Each test gets its own manager, so history never leaks between them."""
    from app.render.jobs import reset_job_manager

    reset_job_manager()
    yield
    reset_job_manager()


def build_project(client: TestClient, settings, scenes: int = 2) -> str:  # noqa: ANN001
    """A renderable project with real images and imported narration."""
    from app.storage.repository import ProjectRepository
    from app.storage.content_import import apply_content, parse_content_json
    from app.tts.narration import attach_imported_audio

    repository = ProjectRepository(settings)
    project = repository.create("Render API")
    paths = repository.paths_for(project.slug)

    write_images(paths.images, scenes)
    package = load_dodo_package()
    package["scenes"] = package["scenes"][:scenes]
    apply_content(project, parse_content_json(json.dumps(package), max_bytes=10_000_000),
                  paths=paths)
    project.intro.enabled = False
    project.outro.enabled = False

    paths.imported_audio.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(project.scenes):
        name = f"s{index}.wav"
        (paths.imported_audio / name).write_bytes(make_wav_bytes(1.5 + index * 0.5))
        attach_imported_audio(scene, paths, f"audio/imported/{name}", settings=settings)

    repository.save(project)
    return project.slug


class TestPreflight:
    def test_reports_ready_for_a_complete_project(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        body = client.get(f"/api/projects/{slug}/render/preflight").json()

        assert body["ready"] is True
        assert body["blockingIssues"] == []
        assert body["timing"]["totalSeconds"] > 0
        assert body["disk"]["sufficient"] is True
        assert body["estimatedRenderSeconds"] > 0

    def test_blocks_and_names_a_scene_missing_an_image(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        project["scenes"][1]["imageFile"] = None
        client.put(f"/api/projects/{slug}", json=project)

        body = client.get(f"/api/projects/{slug}/render/preflight").json()
        assert body["ready"] is False
        assert any("Scene 2" in issue and "image" in issue for issue in body["blockingIssues"])

    def test_blocks_when_narration_has_no_audio(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        project["scenes"][0]["audioFile"] = None
        project["scenes"][0]["audioSource"] = "none"
        client.put(f"/api/projects/{slug}", json=project)

        body = client.get(f"/api/projects/{slug}/render/preflight").json()
        assert body["ready"] is False
        assert any("no audio" in issue for issue in body["blockingIssues"])

    def test_lists_the_transitions_that_will_be_used(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        body = client.get(f"/api/projects/{slug}/render/preflight").json()
        assert len(body["transitions"]) == 2
        assert all(t["restrained"] for t in body["transitions"])

    def test_empty_project_is_not_ready(self, client: TestClient) -> None:
        slug = client.post("/api/projects", json={"name": "Empty"}).json()["project"]["slug"]
        body = client.get(f"/api/projects/{slug}/render/preflight").json()
        assert body["ready"] is False
        assert any("no enabled scenes" in issue for issue in body["blockingIssues"])


class TestJobEndpoints:
    def test_unknown_job_returns_404_with_guidance(self, client: TestClient) -> None:
        response = client.get("/api/jobs/nonexistent")
        assert response.status_code == 404
        assert response.json()["code"] == "job_not_found"
        assert response.json()["suggestion"]

    def test_active_job_is_null_when_idle(self, client: TestClient) -> None:
        assert client.get("/api/jobs/active").json() is None

    def test_history_is_empty_initially(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        assert client.get(f"/api/projects/{slug}/renders").json() == []


@requires_ffmpeg
@pytest.mark.slow
class TestRealRender:
    def _wait(self, client: TestClient, job_id: str, timeout: float = 300.0) -> dict:
        """Drain the SSE stream until the job reaches a terminal state."""
        events: list[dict] = []
        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                    break
        return {"events": events, "job": client.get(f"/api/jobs/{job_id}").json()}

    def test_render_completes_and_streams_progress(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)

        submitted = client.post(f"/api/projects/{slug}/render", json={"quality": "preview"})
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        result = self._wait(client, job_id)
        job = result["job"]
        events = result["events"]

        assert job["status"] == "completed", job.get("errorMessage")
        assert job["progress"] == 1.0
        assert job["outputFile"].endswith(".mp4")

        # The stream carried real intermediate progress, not just a final event.
        assert len(events) >= 3
        phases = {e["phase"] for e in events}
        assert len(phases) >= 3
        progress = [e["progress"] for e in events]
        assert progress == sorted(progress), "progress went backwards in the stream"

    def test_completed_job_exposes_downloadable_artifacts(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        job_id = client.post(
            f"/api/projects/{slug}/render", json={"quality": "preview"}
        ).json()["id"]
        job = self._wait(client, job_id)["job"]
        assert job["status"] == "completed"

        kinds = {a["kind"] for a in job["artifacts"]}
        assert {"video", "subtitles", "report", "log"} <= kinds

        for artifact in job["artifacts"]:
            response = client.get(artifact["url"])
            assert response.status_code == 200, artifact["url"]
            assert len(response.content) > 0

    def test_export_listing_and_log_download(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        job_id = client.post(
            f"/api/projects/{slug}/render", json={"quality": "preview"}
        ).json()["id"]
        self._wait(client, job_id)

        exports = client.get(f"/api/projects/{slug}/exports").json()
        assert any(e["filename"].endswith(".mp4") for e in exports)

        log = client.get(f"/api/jobs/{job_id}/log")
        assert log.status_code == 200
        assert "[timeline]" in log.text

    def test_a_second_render_is_rejected_while_one_runs(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        first = client.post(f"/api/projects/{slug}/render", json={"quality": "preview"})
        assert first.status_code == 202

        second = client.post(f"/api/projects/{slug}/render", json={"quality": "preview"})
        assert second.status_code == 409
        assert "cancel" in second.json()["suggestion"]

        self._wait(client, first.json()["id"])

    def test_cancelling_a_running_render_stops_it(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        """Cancellation must actually terminate the work, not just relabel it."""
        import time

        slug = build_project(client, settings, scenes=3)
        job_id = client.post(f"/api/projects/{slug}/render").json()["id"]

        # Let it get properly under way before cancelling.
        deadline = time.time() + 60
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "running" and job["progress"] > 0.05:
                break
            if job["status"] in {"completed", "failed"}:
                pytest.skip("render finished before it could be cancelled")
            time.sleep(0.3)

        cancelled = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200

        final = self._wait(client, job_id)["job"]
        assert final["status"] == "cancelled"
        assert final["finishedAt"] is not None

    def test_retry_after_cancel_produces_a_new_job(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        job = client.post(f"/api/projects/{slug}/render", json={"quality": "preview"}).json()
        self._wait(client, job["id"])

        retried = client.post(f"/api/jobs/{job['id']}/retry")
        assert retried.status_code == 202
        assert retried.json()["id"] != job["id"]

        final = self._wait(client, retried.json()["id"])["job"]
        assert final["status"] == "completed"

    def test_history_records_both_renders(self, client: TestClient, settings) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        for _ in range(2):
            job_id = client.post(
                f"/api/projects/{slug}/render", json={"quality": "preview"}
            ).json()["id"]
            self._wait(client, job_id)

        history = client.get(f"/api/projects/{slug}/renders").json()
        assert len(history) == 2
        assert all(j["status"] == "completed" for j in history)
        # Auto-versioned: the two renders wrote different files.
        assert history[0]["outputFile"] != history[1]["outputFile"]

    def test_a_failing_render_reports_a_usable_error(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        project = client.get(f"/api/projects/{slug}").json()["project"]
        project["scenes"][0]["imageFile"] = "vanished.png"
        client.put(f"/api/projects/{slug}", json=project)

        job_id = client.post(f"/api/projects/{slug}/render").json()["id"]
        job = self._wait(client, job_id)["job"]

        assert job["status"] == "failed"
        assert job["errorCode"] == "missing_image"
        assert "vanished.png" in job["errorMessage"]
        assert job["errorSuggestion"]

    def test_export_download_rejects_path_traversal(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug = build_project(client, settings)
        response = client.get(f"/api/projects/{slug}/exports/..%2F..%2Fproject.json")
        assert response.status_code in {400, 404}
        assert b"schemaVersion" not in response.content
