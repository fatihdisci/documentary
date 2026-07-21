"""End-to-end render of a complete project.

Builds a real multi-scene project with intro, outro, transitions, burned
subtitles and a music bed, renders it, and validates the actual file. Runs
entirely offline: narration is imported audio, so no TTS service is involved.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.models.enums import JobPhase, MusicSource, TransitionPreset
from app.render.pipeline import RenderPipeline
from app.storage.content_import import apply_content, parse_content_json
from app.storage.repository import ProjectRepository
from app.timing.probe import measure_mean_volume, probe_video
from app.tts.narration import attach_imported_audio
from tests.conftest import requires_ffmpeg
from tests.factories import load_dodo_package, make_wav_bytes, write_images

pytestmark = [requires_ffmpeg, pytest.mark.slow]

#: Deliberately uneven, so a bug that assigns fixed durations would show up.
NARRATION_SECONDS = [2.0, 3.5, 2.2, 4.1, 1.8]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    """Render one real project; every test below inspects the same output."""
    data_dir = tmp_path_factory.mktemp("e2e-data")

    import os

    os.environ["EVB_DATA_DIR"] = str(data_dir)
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()

    repository = ProjectRepository(settings)
    project = repository.create("E2E Dodo")
    paths = repository.paths_for(project.slug)

    write_images(paths.images, 3)
    package = load_dodo_package()
    package["scenes"] = package["scenes"][:3]
    apply_content(project, parse_content_json(json.dumps(package), max_bytes=10_000_000),
                  paths=paths)

    paths.imported_audio.mkdir(parents=True, exist_ok=True)
    units = [("intro", project.intro)] + [(s.id, s) for s in project.scenes] + [
        ("outro", project.outro)
    ]
    for index, (unit_id, unit) in enumerate(units):
        name = f"{unit_id}.wav"
        (paths.imported_audio / name).write_bytes(
            make_wav_bytes(NARRATION_SECONDS[index], freq=180 + index * 40)
        )
        attach_imported_audio(unit, paths, f"audio/imported/{name}", settings=settings)

    project.music.source = MusicSource.GENERATED_AMBIENT
    project.subtitles.burn_in = True
    project.style.transition_preset = TransitionPreset.DOCUMENTARY_DISSOLVE
    repository.save(project)

    events: list[tuple[JobPhase, float, str]] = []
    result = asyncio.run(
        RenderPipeline(
            project, paths, settings=settings,
            on_progress=lambda phase, overall, message: events.append((phase, overall, message)),
        ).run()
    )
    yield result, project, paths, events, repository
    get_settings.cache_clear()


class TestOutput:
    def test_render_succeeds_and_validates(self, rendered) -> None:  # noqa: ANN001
        result = rendered[0]
        assert result.validation.passed, result.validation.format_failures()
        assert result.artifacts.video.is_file()

    def test_every_validation_assertion_passed(self, rendered) -> None:  # noqa: ANN001
        result = rendered[0]
        failures = [a.describe() for a in result.validation.assertions if not a.passed and a.fatal]
        assert failures == []
        # And there were real assertions, not an empty list passing vacuously.
        assert len(result.validation.assertions) >= 12

    def test_resolution_frame_rate_and_codecs(self, rendered) -> None:  # noqa: ANN001
        info = probe_video(rendered[0].artifacts.video)
        assert (info.width, info.height) == (1920, 1080)
        assert info.avg_frame_rate == "60/1"
        assert info.r_frame_rate == "60/1"
        assert info.codec == "h264"
        assert info.pix_fmt == "yuv420p"
        assert info.audio_codec == "aac"
        assert info.audio_sample_rate == 48_000

    def test_duration_matches_the_computed_timeline(self, rendered) -> None:  # noqa: ANN001
        result = rendered[0]
        info = probe_video(result.artifacts.video)
        assert info.duration_seconds == pytest.approx(
            result.timeline.total_duration_seconds, abs=0.2
        )

    def test_transition_overlap_shortens_the_video(self, rendered) -> None:  # noqa: ANN001
        """Proof the timeline accounts for overlap rather than summing naively."""
        result = rendered[0]
        timeline = result.timeline
        naive_total = sum(e.duration_seconds for e in timeline.entries)
        assert timeline.transition_total_seconds > 0
        assert timeline.total_duration_seconds == pytest.approx(
            naive_total - timeline.transition_total_seconds + timeline.audio_tail_seconds,
            abs=0.01,
        )

    def test_narration_is_not_truncated(self, rendered) -> None:  # noqa: ANN001
        result = rendered[0]
        info = probe_video(result.artifacts.video)
        assert info.duration_seconds >= result.timeline.last_narration_end

    def test_audio_is_present_and_not_silent(self, rendered) -> None:  # noqa: ANN001
        mean_volume = measure_mean_volume(rendered[0].artifacts.video)
        assert mean_volume is not None and mean_volume > -50.0

    def test_scene_durations_follow_their_own_audio(self, rendered) -> None:  # noqa: ANN001
        """Fixed-duration scenes would show up as identical values here."""
        timeline = rendered[0].timeline
        durations = [round(e.duration_seconds, 2) for e in timeline.entries]
        assert len(set(durations)) == len(durations), f"durations repeat: {durations}"


class TestArtifacts:
    def test_all_expected_files_are_written(self, rendered) -> None:  # noqa: ANN001
        artifacts = rendered[0].artifacts
        for name in (
            "subtitles", "narration_audio", "description",
            "thumbnail_prompt", "project_snapshot", "render_log", "report",
        ):
            path = getattr(artifacts, name)
            assert path is not None and path.is_file(), f"{name} was not written"
            assert path.stat().st_size > 0

    def test_per_scene_subtitles_are_written(self, rendered) -> None:  # noqa: ANN001
        assert len(rendered[0].artifacts.scene_subtitles) >= 3
        for path in rendered[0].artifacts.scene_subtitles:
            assert "-->" in path.read_text("utf-8")

    def test_full_srt_is_valid(self, rendered) -> None:  # noqa: ANN001
        import re

        text = rendered[0].artifacts.subtitles.read_text("utf-8")
        stamps = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", text)
        assert len(stamps) >= 3

        def seconds(value: str) -> float:
            hours, minutes, rest = value.split(":")
            secs, millis = rest.split(",")
            return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000

        previous_end = -1.0
        for start, end in stamps:
            assert seconds(end) > seconds(start), "inverted cue"
            assert seconds(start) >= previous_end, "overlapping cues"
            previous_end = seconds(end)

    def test_report_records_the_checksum_and_assertions(self, rendered) -> None:  # noqa: ANN001
        report = json.loads(rendered[0].artifacts.report.read_text("utf-8"))
        assert len(report["checksum"]) == 64
        assert report["validation"]["passed"] is True
        assert len(report["validation"]["assertions"]) >= 12
        assert report["timeline"]["totalSeconds"] > 0

    def test_render_log_records_every_stage(self, rendered) -> None:  # noqa: ANN001
        log = rendered[0].artifacts.render_log.read_text("utf-8")
        assert "[timeline]" in log
        assert "[clip]" in log
        assert "[disk]" in log
        assert "ffmpeg" in log.lower()

    def test_project_snapshot_reopens(self, rendered) -> None:  # noqa: ANN001
        from app.models.project import Project

        snapshot = json.loads(rendered[0].artifacts.project_snapshot.read_text("utf-8"))
        assert Project.model_validate(snapshot).slug == rendered[1].slug


class TestProgress:
    def test_progress_covers_the_pipeline(self, rendered) -> None:  # noqa: ANN001
        events = rendered[3]
        phases = {phase for phase, _, _ in events}
        assert JobPhase.RENDER_SCENE_CLIPS in phases
        assert JobPhase.ENCODE in phases
        assert JobPhase.VALIDATE_OUTPUT in phases

    def test_progress_only_moves_forward(self, rendered) -> None:  # noqa: ANN001
        values = [overall for _, overall, _ in rendered[3]]
        assert values == sorted(values), "progress went backwards"
        assert 0.0 <= min(values) and max(values) <= 1.0

    def test_progress_messages_are_human_readable(self, rendered) -> None:  # noqa: ANN001
        for _, _, message in rendered[3]:
            assert message and not message.startswith("<")


class TestCaching:
    def test_second_render_reuses_every_clip(self, rendered) -> None:  # noqa: ANN001
        """The whole point of the per-scene cache."""
        result, project, paths, _, repository = rendered
        assert result.rendered_clips > 0

        again = asyncio.run(RenderPipeline(project, paths).run())
        assert again.rendered_clips == 0
        assert again.reused_clips == result.reused_clips + result.rendered_clips

    def test_export_is_auto_versioned_and_never_overwrites(self, rendered) -> None:  # noqa: ANN001
        result, project, paths, _, _ = rendered
        first = result.artifacts.video
        assert first.is_file()
        original_bytes = first.stat().st_size

        again = asyncio.run(RenderPipeline(project, paths).run())

        assert again.artifacts.video != first
        assert first.is_file(), "the earlier export was destroyed"
        assert first.stat().st_size == original_bytes
        assert again.artifacts.video.name > first.name

    def test_changing_export_quality_does_not_rebuild_clips(self, rendered) -> None:  # noqa: ANN001
        """Cache matrix: export quality touches the final encode only."""
        from app.models.enums import QualityPreset

        _, project, paths, _, repository = rendered
        project.export.quality = QualityPreset.PREVIEW
        repository.save(project)

        result = asyncio.run(RenderPipeline(project, paths).run())
        assert result.rendered_clips == 0

    def test_changing_a_title_rebuilds_only_that_clip(self, rendered) -> None:  # noqa: ANN001
        _, project, paths, _, repository = rendered
        project.scenes[0].title = "A Completely Different Title"
        repository.save(project)

        result = asyncio.run(RenderPipeline(project, paths).run())
        assert result.rendered_clips == 1, "only the edited scene should re-render"
        assert result.reused_clips == len(result.scene_clips) - 1


class TestFailureHandling:
    def test_a_missing_image_names_the_scene(self, rendered) -> None:  # noqa: ANN001
        from app.errors import AppError, ErrorCode

        _, project, paths, _, repository = rendered
        copy = project.model_copy(deep=True)
        copy.scenes[1].image_file = "not-there.png"

        with pytest.raises(AppError) as exc_info:
            asyncio.run(RenderPipeline(copy, paths).run())
        assert exc_info.value.code is ErrorCode.MISSING_IMAGE
        assert "not-there.png" in exc_info.value.message

    def test_cancellation_stops_the_render(self, rendered) -> None:  # noqa: ANN001
        from app.render.ffmpeg import CancelledRender

        _, project, paths, _, _ = rendered
        copy = project.model_copy(deep=True)
        # Force real work rather than a cache hit.
        copy.scenes[0].title = "Cancel me now please"

        async def run_and_cancel() -> None:
            cancel = asyncio.Event()
            pipeline = RenderPipeline(copy, paths, cancel_event=cancel)
            task = asyncio.create_task(pipeline.run())
            await asyncio.sleep(0.4)
            cancel.set()
            await task

        with pytest.raises(CancelledRender):
            asyncio.run(run_and_cancel())
