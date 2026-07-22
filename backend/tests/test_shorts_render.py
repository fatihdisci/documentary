"""End-to-end Shorts: real FFmpeg, real output, real pixels.

The source used here has one flat colour per section, so what ends up in the
finished Short can be asserted rather than assumed: which section came first,
whether a transition boundary survived inside a contiguous cut, and whether the
picture really is letterboxed on black instead of stretched.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.enums import JobStatus
from app.shorts.manifest import ManifestEntry
from app.shorts.pipeline import ShortsPipeline
from app.shorts.plan import build_plan
from app.storage.repository import ProjectRepository
from app.timing.probe import measure_mean_volume, probe_video
from tests.conftest import requires_ffmpeg
from tests.shorts_factories import build_entries, make_manifest, request_for, write_manifest

pytestmark = requires_ffmpeg

SECTION_SECONDS = 4.0
TRANSITION = 0.5
FPS = 30
SOURCE_WIDTH = 640
SOURCE_HEIGHT = 360

#: One well-separated colour per section, so a frame identifies its section.
COLOURS: list[tuple[str, tuple[int, int, int]]] = [
    ("0x202020", (32, 32, 32)),     # 0 intro
    ("0xE00000", (224, 0, 0)),      # 1
    ("0x00C000", (0, 192, 0)),      # 2
    ("0x0000E0", (0, 0, 224)),      # 3
    ("0xE0E000", (224, 224, 0)),    # 4
    ("0xE000E0", (224, 0, 224)),    # 5 outro
]


@pytest.fixture(autouse=True)
def fresh_managers():  # noqa: ANN201
    from app.render.jobs import reset_job_manager
    from app.render.slot import reset_render_slot
    from app.shorts.jobs import reset_short_job_manager

    reset_job_manager()
    reset_short_job_manager()
    reset_render_slot()
    yield
    reset_job_manager()
    reset_short_job_manager()
    reset_render_slot()


def make_colour_source(path: Path, entries: list[ManifestEntry], settings) -> Path:  # noqa: ANN001
    """A video whose colour changes exactly at each section's start time."""
    spans: list[tuple[str, float]] = []
    for index, entry in enumerate(entries):
        nxt = entries[index + 1].start_seconds if index + 1 < len(entries) else entry.end_seconds
        spans.append((COLOURS[index % len(COLOURS)][0], round(nxt - entry.start_seconds, 4)))

    total = entries[-1].end_seconds
    args = [settings.require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    for colour, seconds in spans:
        args += [
            "-f", "lavfi",
            "-i", f"color=c={colour}:size={SOURCE_WIDTH}x{SOURCE_HEIGHT}:rate={FPS}:duration={seconds:g}",
        ]
    args += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={total:g}"]

    chain = "".join(f"[{i}:v]" for i in range(len(spans)))
    args += [
        "-filter_complex", f"{chain}concat=n={len(spans)}:v=1:a=0[v]",
        "-map", "[v]", "-map", f"{len(spans)}:a",
        "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-r", str(FPS), "-fps_mode", "cfr", "-t", f"{total:g}",
        "-movflags", "+faststart", str(path),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True, capture_output=True)  # noqa: S603
    return path


def sample_pixel(video: Path, at: float, x: int, y: int, settings) -> tuple[int, int, int]:  # noqa: ANN001
    """Read one pixel out of a rendered frame."""
    result = subprocess.run(  # noqa: S603
        [
            settings.require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", f"crop=2:2:{x}:{y}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True, capture_output=True,
    )
    data = result.stdout
    assert len(data) >= 3, "no pixel data came back"
    return (data[0], data[1], data[2])


def close_to(actual: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 40) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


@pytest.fixture
def source(settings):  # noqa: ANN001, ANN201
    """A project with one finished long render and its manifest."""
    repository = ProjectRepository(settings)
    project = repository.create("Shorts Render")
    paths = repository.paths_for(project.slug)
    paths.ensure()

    entries, total = build_entries(
        scene_count=4, scene_duration=SECTION_SECONDS, transition=TRANSITION
    )
    video = paths.exports / f"{project.slug}_v01.mp4"
    make_colour_source(video, entries, settings)
    manifest = make_manifest(
        video, slug=project.slug, entries=entries, total=total,
        fps=FPS, width=SOURCE_WIDTH, height=SOURCE_HEIGHT,
    )
    write_manifest(manifest, video)
    return project.slug, paths, manifest


async def build_short(source, settings, *units, trims=None):  # noqa: ANN001, ANN201
    slug, paths, manifest = source
    request = request_for(*units, trims=trims)
    plan = build_plan(manifest, request)
    pipeline = ShortsPipeline(
        paths=paths, manifest=manifest, request=request, plan=plan,
        settings=settings, job_id=f"test-{'-'.join(units)}",
    )
    return await pipeline.run()


class TestOutput:
    def test_output_is_a_valid_vertical_short(self, source, settings) -> None:  # noqa: ANN001
        result = asyncio.run(build_short(source, settings, "scene-2"))
        video = result.artifacts.video

        info = probe_video(video, settings=settings)
        assert (info.width, info.height) == (1080, 1920)
        assert info.codec == "h264"
        assert info.pix_fmt == "yuv420p"
        assert info.avg_frame_rate == f"{FPS}/1"
        assert info.r_frame_rate == f"{FPS}/1"
        assert info.has_audio
        assert info.audio_codec == "aac"
        assert info.audio_sample_rate == 48_000
        assert info.duration_seconds == pytest.approx(
            result.plan.total_duration_seconds, abs=0.35
        )
        assert result.validation.passed

    def test_audio_carries_through_from_the_source_mix(self, source, settings) -> None:  # noqa: ANN001
        result = asyncio.run(build_short(source, settings, "scene-2"))
        mean_volume = measure_mean_volume(result.artifacts.video, settings=settings)
        assert mean_volume is not None and mean_volume > -60.0

    def test_measured_frame_intervals_are_constant(self, source, settings) -> None:  # noqa: ANN001
        result = asyncio.run(build_short(source, settings, "scene-2"))
        named = {a.name: a for a in result.validation.assertions}
        assert named["measured frame intervals"].passed

    def test_source_sits_centred_on_black_with_its_aspect_kept(
        self, source, settings
    ) -> None:  # noqa: ANN001
        result = asyncio.run(build_short(source, settings, "scene-2"))
        video = result.artifacts.video
        geometry = result.validation.geometry

        # 640x360 is 16:9, so it lands at 1080x608 with 656 rows of black above
        # and below — never stretched to fill, never cropped.
        assert (geometry.inner_width, geometry.inner_height) == (1080, 608)
        assert geometry.offset_x == 0 and geometry.offset_y == 656

        at = result.plan.total_duration_seconds / 2
        assert close_to(sample_pixel(video, at, 20, 20, settings), (0, 0, 0), tol=12)
        assert close_to(sample_pixel(video, at, 1000, 1880, settings), (0, 0, 0), tol=12)
        # ...and the picture itself is right where the geometry says it is.
        assert close_to(sample_pixel(video, at, 540, 960, settings), COLOURS[2][1])

    def test_the_short_is_published_atomically_with_its_side_cars(
        self, source, settings
    ) -> None:  # noqa: ANN001
        slug, paths, _ = source
        result = asyncio.run(build_short(source, settings, "scene-2"))

        assert result.artifacts.video.parent == paths.shorts_exports
        assert result.artifacts.manifest.is_file()
        assert result.artifacts.log.is_file()
        # No scratch left behind, and nothing partial in the exports folder.
        assert not (paths.shorts_cache / "work" / "test-scene-2").exists()
        assert not list(paths.shorts_exports.glob("*.partial*"))

        written = json.loads(result.artifacts.manifest.read_text("utf-8"))
        assert written["shortId"] == result.plan.cache_key
        assert written["width"] == 1080 and written["height"] == 1920
        assert written["sourceVideo"].endswith(".mp4")
        assert written["validation"]["passed"] is True

    def test_long_render_exports_are_never_written_to(self, source, settings) -> None:  # noqa: ANN001
        slug, paths, manifest = source
        before = {p.name: p.stat().st_mtime_ns for p in paths.exports.iterdir() if p.is_file()}
        asyncio.run(build_short(source, settings, "scene-2"))
        after = {p.name: p.stat().st_mtime_ns for p in paths.exports.iterdir() if p.is_file()}
        assert before == after


class TestOrderingAndTransitions:
    def test_selection_order_decides_playback_order(self, source, settings) -> None:  # noqa: ANN001
        """Picking 4 then 1 must play section 4 first."""
        result = asyncio.run(build_short(source, settings, "scene-4", "scene-1"))
        video = result.artifacts.video
        first, second = result.plan.groups

        assert first.numbers == [4] and second.numbers == [1]
        assert close_to(
            sample_pixel(video, first.duration_seconds / 2, 540, 960, settings), COLOURS[4][1]
        )
        assert close_to(
            sample_pixel(
                video, first.duration_seconds + second.duration_seconds / 2, 540, 960, settings
            ),
            COLOURS[1][1],
        )

    def test_a_contiguous_pair_keeps_the_boundary_inside_one_cut(
        self, source, settings
    ) -> None:  # noqa: ANN001
        slug, paths, manifest = source
        result = asyncio.run(build_short(source, settings, "scene-2", "scene-3"))
        video = result.artifacts.video

        assert len(result.plan.groups) == 1
        group = result.plan.groups[0]
        assert group.preserved_transitions == 1

        # The colour change lands exactly where the source's section boundary
        # sits inside the cut — the span between them was carried through whole.
        boundary = manifest.entry("scene-3").start_seconds - group.start_seconds
        assert close_to(sample_pixel(video, boundary - 0.6, 540, 960, settings), COLOURS[2][1])
        assert close_to(sample_pixel(video, boundary + 0.6, 540, 960, settings), COLOURS[3][1])

    def test_a_non_contiguous_selection_carries_no_neighbouring_frames(
        self, source, settings
    ) -> None:  # noqa: ANN001
        """Sections 1 and 3 must contain nothing from section 2."""
        result = asyncio.run(build_short(source, settings, "scene-1", "scene-3"))
        video = result.artifacts.video
        first, second = result.plan.groups

        # Sample densely across both cuts: only colours 1 and 3 may appear.
        step = 0.25
        seen: list[tuple[int, int, int]] = []
        at = 0.1
        while at < result.plan.total_duration_seconds - 0.1:
            seen.append(sample_pixel(video, at, 540, 960, settings))
            at += step

        assert seen, "nothing was sampled"
        for pixel in seen:
            assert close_to(pixel, COLOURS[1][1]) or close_to(pixel, COLOURS[3][1]), (
                f"frame carried a colour from an unselected section: {pixel}"
            )
        del first, second

    def test_trimming_stays_inside_the_selected_section(self, source, settings) -> None:  # noqa: ANN001
        slug, paths, manifest = source
        scene = manifest.entry("scene-3")
        result = asyncio.run(
            build_short(
                source, settings, "scene-3",
                trims={"scene-3": (scene.safe_start_seconds + 0.5, scene.safe_end_seconds - 0.5)},
            )
        )
        info = probe_video(result.artifacts.video, settings=settings)
        assert info.duration_seconds == pytest.approx(
            scene.safe_duration_seconds - 1.0, abs=0.3
        )
        assert close_to(
            sample_pixel(result.artifacts.video, info.duration_seconds / 2, 540, 960, settings),
            COLOURS[3][1],
        )


class TestFailureHandling:
    def test_a_cancelled_short_leaves_nothing_behind(self, source, settings) -> None:  # noqa: ANN001
        from app.render.ffmpeg import CancelledRender

        slug, paths, manifest = source
        request = request_for("scene-1", "scene-3")
        plan = build_plan(manifest, request)

        cancel = asyncio.Event()
        cancel.set()  # cancelled before the first command even starts
        pipeline = ShortsPipeline(
            paths=paths, manifest=manifest, request=request, plan=plan,
            settings=settings, cancel_event=cancel, job_id="cancelled-job",
        )
        with pytest.raises(CancelledRender):
            asyncio.run(pipeline.run())

        assert not list(paths.shorts_exports.glob("*.mp4"))
        assert not (paths.shorts_cache / "work" / "cancelled-job").exists()

    def test_a_source_deleted_after_planning_fails_as_stale(
        self, source, settings
    ) -> None:  # noqa: ANN001
        from app.errors import AppError, ErrorCode

        slug, paths, manifest = source
        request = request_for("scene-2")
        plan = build_plan(manifest, request)
        (paths.exports / manifest.source.filename).unlink()

        pipeline = ShortsPipeline(
            paths=paths, manifest=manifest, request=request, plan=plan,
            settings=settings, job_id="stale-job",
        )
        with pytest.raises(AppError) as exc:
            asyncio.run(pipeline.run())
        assert exc.value.code is ErrorCode.STALE_RENDER
        assert not list(paths.shorts_exports.glob("*.mp4"))

    def test_a_source_edited_after_planning_fails_as_stale(
        self, source, settings
    ) -> None:  # noqa: ANN001
        from app.errors import AppError, ErrorCode

        slug, paths, manifest = source
        request = request_for("scene-2")
        plan = build_plan(manifest, request)

        video = paths.exports / manifest.source.filename
        video.write_bytes(video.read_bytes() + b"appended")

        pipeline = ShortsPipeline(
            paths=paths, manifest=manifest, request=request, plan=plan,
            settings=settings, job_id="edited-job",
        )
        with pytest.raises(AppError) as exc:
            asyncio.run(pipeline.run())
        assert exc.value.code is ErrorCode.STALE_RENDER


class TestThroughTheApi:
    def _wait(self, client: TestClient, job_id: str, timeout: float = 180.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = client.get(f"/api/short-jobs/{job_id}").json()
            if body["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return body
            time.sleep(0.25)
        raise AssertionError(f"Short job {job_id} did not finish in {timeout}s")

    def _project(self, client: TestClient, settings) -> tuple[str, object]:  # noqa: ANN001
        repository = ProjectRepository(settings)
        project = repository.create("Shorts HTTP")
        paths = repository.paths_for(project.slug)
        paths.ensure()
        entries, total = build_entries(
            scene_count=4, scene_duration=SECTION_SECONDS, transition=TRANSITION
        )
        video = paths.exports / f"{project.slug}_v01.mp4"
        make_colour_source(video, entries, settings)
        manifest = make_manifest(
            video, slug=project.slug, entries=entries, total=total,
            fps=FPS, width=SOURCE_WIDTH, height=SOURCE_HEIGHT,
        )
        write_manifest(manifest, video)
        return project.slug, manifest

    def test_full_round_trip_then_download_and_delete(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = self._project(client, settings)

        preflight = client.post(
            f"/api/projects/{slug}/shorts/preflight",
            json={
                "sourceRenderId": "render0001",
                "segments": [{"unitId": "scene-2"}, {"unitId": "scene-3"}],
            },
        ).json()
        assert preflight["ready"] is True
        assert preflight["plan"]["groups"][0]["preservedTransitions"] == 1
        assert preflight["totalDurationSeconds"] > 0
        assert preflight["maxSeconds"] == 180.0
        assert preflight["previewFrames"], "no preview frame was produced"

        frame = client.get(preflight["previewFrames"][0]["url"])
        assert frame.status_code == 200
        assert frame.headers["content-type"] == "image/jpeg"

        created = client.post(
            f"/api/projects/{slug}/shorts",
            json={
                "sourceRenderId": "render0001",
                "segments": [{"unitId": "scene-2"}, {"unitId": "scene-3"}],
            },
        )
        assert created.status_code == 202
        job = self._wait(client, created.json()["id"])
        assert job["status"] == "completed", job.get("errorMessage")
        assert job["cacheReused"] is False
        assert job["groupCount"] == 1
        assert {a["kind"] for a in job["artifacts"]} >= {"video", "manifest", "log"}

        listing = client.get(f"/api/projects/{slug}/shorts").json()
        assert len(listing) == 1
        record = listing[0]
        assert record["sectionNumbers"] == [2, 3]

        download = client.get(record["url"])
        assert download.status_code == 200
        assert len(download.content) == record["sizeBytes"]

        log = client.get(f"/api/short-jobs/{job['id']}/log")
        assert log.status_code == 200
        assert "[compose]" in log.text

        deleted = client.delete(f"/api/projects/{slug}/shorts/{record['shortId']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/projects/{slug}/shorts").json() == []
        # The long render is untouched.
        assert client.get(f"/api/projects/{slug}/shorts/sources").json()

    def test_resubmitting_the_same_request_reuses_the_result(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = self._project(client, settings)
        payload = {"sourceRenderId": "render0001", "segments": [{"unitId": "scene-1"}]}

        first = client.post(f"/api/projects/{slug}/shorts", json=payload).json()
        self._wait(client, first["id"])

        second = client.post(f"/api/projects/{slug}/shorts", json=payload).json()
        assert second["status"] == "completed"
        assert second["cacheReused"] is True
        assert second["shortId"] == first["shortId"]

        paths = ProjectRepository(settings).paths_for(slug)
        assert len(list(paths.shorts_exports.glob("*.mp4"))) == 1

    def test_the_event_stream_reports_progress_then_completion(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = self._project(client, settings)
        job_id = client.post(
            f"/api/projects/{slug}/shorts",
            json={"sourceRenderId": "render0001", "segments": [{"unitId": "scene-2"}]},
        ).json()["id"]

        events: list[dict] = []
        with client.stream("GET", f"/api/short-jobs/{job_id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                    break

        assert events[-1]["status"] == "completed"
        assert len(events) >= 2
        progress = [e["progress"] for e in events]
        assert progress == sorted(progress), "progress went backwards"

    def test_retry_reuses_the_same_request_definition(
        self, client: TestClient, settings
    ) -> None:  # noqa: ANN001
        slug, _ = self._project(client, settings)
        payload = {
            "sourceRenderId": "render0001",
            "segments": [{"unitId": "scene-3"}, {"unitId": "scene-1"}],
        }
        first = client.post(f"/api/projects/{slug}/shorts", json=payload).json()
        self._wait(client, first["id"])

        retried = client.post(f"/api/short-jobs/{first['id']}/retry")
        assert retried.status_code == 202
        body = retried.json()
        assert body["request"]["segments"] == first["request"]["segments"]
        assert body["cacheKey"] == first["cacheKey"]
