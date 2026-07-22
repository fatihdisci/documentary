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


class TestShortsSourcePackage:
    """The opt-in clean master, produced beside a captioned export.

    This project burns subtitles in, so the export is permanently captioned and
    a second subtitle-free pass really has to run. What matters is that the
    normal export is completely unaffected by it, and that the package it leaves
    behind is verifiable rather than inferred.
    """

    def package(self, rendered):  # noqa: ANN001, ANN202
        from app.shorts.manifest import load_manifest

        manifest = load_manifest(rendered[0].artifacts.manifest)
        assert manifest.shorts_source is not None, "no clean master was prepared"
        return manifest, manifest.shorts_source

    def test_the_manifest_is_v2_and_carries_the_package(self, rendered) -> None:  # noqa: ANN001
        from app.shorts.manifest import MANIFEST_SCHEMA_VERSION

        manifest, package = self.package(rendered)
        assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
        assert manifest.source_has_burned_in_subtitles is True
        assert manifest.supports_native_captions is True
        assert package.origin == "dedicated-pass"

    def test_the_clean_master_is_a_real_separate_file(self, rendered) -> None:  # noqa: ANN001
        _, _, paths, _, _ = rendered
        manifest, package = self.package(rendered)

        master = paths.shorts_source / package.clean_master.filename
        assert master.is_file()
        assert master != rendered[0].artifacts.video
        assert package.clean_master.sha256 != manifest.source.sha256, (
            "a subtitle-free pass must not produce the captioned export's bytes"
        )

    def test_it_matches_the_export_frame_for_frame(self, rendered) -> None:  # noqa: ANN001
        """Same timeline, same geometry, same rate, same audio — only no captions."""
        _, _, paths, _, _ = rendered
        manifest, package = self.package(rendered)
        master = paths.shorts_source / package.clean_master.filename

        clean = probe_video(master)
        export = probe_video(rendered[0].artifacts.video)

        assert (clean.width, clean.height) == (export.width, export.height)
        assert clean.avg_frame_rate == export.avg_frame_rate
        assert clean.codec == export.codec == "h264"
        assert clean.pix_fmt == export.pix_fmt
        assert clean.audio_codec == export.audio_codec
        assert clean.audio_sample_rate == export.audio_sample_rate
        assert clean.duration_seconds == pytest.approx(export.duration_seconds, abs=0.35)

    def test_the_clean_master_carries_the_same_audio_mix(self, rendered) -> None:  # noqa: ANN001
        _, _, paths, _, _ = rendered
        _, package = self.package(rendered)
        master = paths.shorts_source / package.clean_master.filename

        clean = measure_mean_volume(master)
        export = measure_mean_volume(rendered[0].artifacts.video)
        assert clean is not None and export is not None
        assert clean == pytest.approx(export, abs=1.0)

    def test_it_verifies_against_its_own_manifest(self, rendered) -> None:  # noqa: ANN001
        from app.shorts.manifest import verify_clean_master

        _, _, paths, _, _ = rendered
        manifest, package = self.package(rendered)
        master = paths.shorts_source / package.clean_master.filename

        verified = verify_clean_master(manifest, master)
        assert verified.cue_sidecar.cue_count > 0

    def test_the_cue_sidecar_holds_the_render_s_own_cues(self, rendered) -> None:  # noqa: ANN001
        from app.shorts.cues import load_sidecar

        result, _, paths, _, _ = rendered
        _, package = self.package(rendered)
        sidecar = load_sidecar(
            paths.shorts_source / package.cue_sidecar.filename, package.cue_sidecar
        )

        assert len(sidecar.cues) == len(result.timeline.cues)
        assert sidecar.clean_master_sha256 == package.clean_master.sha256

        # Absolute times, straight off the timeline, in order, with the unit each
        # one belongs to. This is what a Short rebases.
        for recorded, original in zip(sidecar.cues, result.timeline.cues, strict=True):
            assert recorded.start_seconds == pytest.approx(original.start_seconds, abs=0.001)
            assert recorded.end_seconds == pytest.approx(original.end_seconds, abs=0.001)
            assert recorded.lines == original.lines
            assert recorded.unit_id, "every cue records the section it came from"

    def test_the_sidecar_lives_beside_the_master_not_in_exports(self, rendered) -> None:  # noqa: ANN001
        """Neither file clutters the user's export list."""
        _, _, paths, _, _ = rendered
        _, package = self.package(rendered)

        top_level = {p.name for p in paths.exports.iterdir() if p.is_file()}
        assert package.clean_master.filename not in top_level
        assert package.cue_sidecar.filename not in top_level
        assert (paths.shorts_source / package.cue_sidecar.filename).is_file()

    def test_the_normal_export_is_unchanged_by_all_of_this(self, rendered) -> None:  # noqa: ANN001
        """The published long video is the same file it has always been."""
        result, project, paths, _, _ = rendered

        assert result.artifacts.video.parent == paths.exports
        assert result.artifacts.video.name.startswith(project.slug)
        assert result.artifacts.video.suffix == ".mp4"
        assert result.validation.passed
        # The .srt files are still user artifacts, produced exactly as before.
        assert result.artifacts.subtitles.is_file()
        assert len(result.artifacts.scene_subtitles) >= 3

    def test_the_clean_clips_have_their_own_cache_namespace(self, rendered) -> None:  # noqa: ANN001
        """Neither clip cache may evict the other's files."""
        from app.render.clean_master import CLEAN_MASTER_CACHE_SLUG

        _, _, paths, _, _ = rendered
        captioned = [p for p in paths.clips.iterdir() if p.is_file()]
        clean = list((paths.clips / CLEAN_MASTER_CACHE_SLUG).glob("*"))

        assert captioned, "the captioned export's clips are still cached"
        assert clean, "the clean master's clips are cached separately"


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

    def test_changing_between_full_qualities_does_not_rebuild_clips(self, rendered) -> None:  # noqa: ANN001
        """Cache matrix: a full-quality preset touches the final encode only.

        The fixture rendered at the default (youtube-hq). Switching to another
        full-quality preset shares the same scene-clip geometry, so nothing
        re-renders. Preview is deliberately excluded — it has its own lighter
        cache (see below).
        """
        from app.models.enums import QualityPreset

        _, project, paths, _, repository = rendered
        project.export.quality = QualityPreset.STANDARD
        repository.save(project)

        result = asyncio.run(RenderPipeline(project, paths).run())
        assert result.rendered_clips == 0

    def test_preview_builds_its_own_cache_then_reuses_it(self, rendered) -> None:  # noqa: ANN001
        """Preview renders its own lighter clips, apart from the full cache.

        The first preview render builds every clip fresh (different frame rate and
        supersample), and crucially does *not* evict the full-quality clips the
        fixture already built. A second preview reuses them.
        """
        from app.models.enums import QualityPreset

        _, project, paths, _, repository = rendered
        full_clips = sorted(p.name for p in paths.clips.glob("*.mp4"))

        project.export.quality = QualityPreset.PREVIEW
        repository.save(project)

        first = asyncio.run(RenderPipeline(project, paths).run())
        assert first.rendered_clips == len(first.scene_clips)
        assert first.reused_clips == 0
        # The expensive full-quality cache survives the preview untouched.
        assert sorted(p.name for p in paths.clips.glob("*.mp4")) == full_clips
        assert all("preview" in str(c.path.parent) for c in first.scene_clips)

        second = asyncio.run(RenderPipeline(project, paths).run())
        assert second.reused_clips == len(second.scene_clips)
        assert second.rendered_clips == 0

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
