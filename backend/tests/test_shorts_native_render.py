"""End-to-end Shorts-native captions: real FFmpeg, real pixels.

The source here is two flat-coloured sections, and the "clean master" is a
*different* flat colour from the captioned export. That makes the central claim
checkable rather than assumed: a native Short must be built from the clean
master, so the picture in the output has to be the clean master's colour, and a
legacy Short of the same span has to be the export's.

Caption placement is checked the same way — by reading pixels out of the
finished file, in the black band below the 16:9 picture, where nothing but a
caption could have put them.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.errors import AppError, ErrorCode
from app.shorts.captions import MAX_INLINE_CAPTION_OVERLAYS
from app.shorts.models import (
    ShortCaptionMode,
    ShortCaptionPreset,
    ShortCaptionStyle,
    ShortRequest,
    resolve_caption_style,
)
from app.shorts.pipeline import ShortsPipeline
from app.shorts.plan import build_plan
from app.shorts.service import ShortsService
from app.shorts.validate import fit_geometry
from app.storage.repository import ProjectRepository
from app.timing.probe import measure_mean_volume, probe_video
from tests.conftest import requires_ffmpeg
from tests.shorts_factories import (
    build_entries,
    cues_for,
    make_manifest,
    make_shorts_source,
    request_for,
    write_manifest,
    write_shorts_source,
)

pytestmark = requires_ffmpeg

FPS = 30
SOURCE_WIDTH = 640
SOURCE_HEIGHT = 360
SCENE_SECONDS = 4.0
TRANSITION = 0.5

#: Well separated, so one pixel identifies which file the picture came from.
EXPORT_COLOUR = ("0xE00000", (224, 0, 0))
MASTER_COLOUR = ("0x0000E0", (0, 0, 224))


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


def make_flat_source(path: Path, colour: str, seconds: float, settings) -> Path:  # noqa: ANN001
    """One flat colour plus a tone, at the source geometry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - argument list, never a shell
        [
            settings.require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"color=c={colour}:size={SOURCE_WIDTH}x{SOURCE_HEIGHT}:"
                  f"rate={FPS}:duration={seconds:g}",
            "-f", "lavfi",
            "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds:g}",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-r", str(FPS), "-fps_mode", "cfr", "-t", f"{seconds:g}",
            "-movflags", "+faststart", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def sample_pixel(video: Path, at: float, x: int, y: int, settings) -> tuple[int, int, int]:  # noqa: ANN001
    result = subprocess.run(  # noqa: S603
        [
            settings.require_tool("ffmpeg"), "-hide_banner", "-nostdin",
            "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", f"crop=2:2:{x}:{y}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True, capture_output=True,
    )
    assert len(result.stdout) >= 3, "no pixel data came back"
    return (result.stdout[0], result.stdout[1], result.stdout[2])


def close_to(actual, expected, tol: int = 40) -> bool:  # noqa: ANN001
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


def brightest_in(
    video: Path, at: float, *, x: int, y: int, width: int, height: int, settings
) -> int:  # noqa: ANN001
    """Brightest channel value anywhere in a region of one frame.

    A region, not a pixel: a caption is white glyphs on a dark box, so most
    points inside it are still dark and a single-pixel probe would be a coin
    flip on where a letter stroke happens to fall.
    """
    result = subprocess.run(  # noqa: S603
        [
            settings.require_tool("ffmpeg"), "-hide_banner", "-nostdin",
            "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", f"crop={width}:{height}:{x}:{y}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True, capture_output=True,
    )
    assert result.stdout, "no pixel data came back"
    return max(result.stdout)


@pytest.fixture
def prepared(settings):  # noqa: ANN001, ANN201
    """A finished render *with* a Shorts source package, both files real."""
    repository = ProjectRepository(settings)
    project = repository.create("Native Captions")
    paths = repository.paths_for(project.slug)
    paths.ensure()

    entries, total = build_entries(
        scene_count=2, scene_duration=SCENE_SECONDS,
        with_intro=False, with_outro=False, transition=TRANSITION,
    )
    export = make_flat_source(
        paths.exports / f"{project.slug}_v01.mp4", EXPORT_COLOUR[0], total, settings
    )
    manifest = make_manifest(
        export, slug=project.slug, entries=entries, total=total,
        fps=FPS, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, duration_seconds=total,
    )

    master = make_flat_source(
        paths.shorts_source / f"{project.slug}_v01-clean.mp4",
        MASTER_COLOUR[0], total, settings,
    )
    package, sidecar = make_shorts_source(
        master, manifest=manifest, cues=cues_for(entries, per_section=2)
    )
    manifest.shorts_source = write_shorts_source(package, sidecar, master)
    write_manifest(manifest, export)

    return project.slug, paths, manifest, master


async def build(prepared, settings, *units, mode, preset=None, trims=None):  # noqa: ANN001, ANN201
    slug, paths, manifest, master = prepared
    request = ShortRequest(
        source_render_id=manifest.render_job_id,
        segments=request_for(*units, trims=trims).segments,
        caption_mode=mode,
        caption_style=ShortCaptionStyle(preset=preset) if preset else None,
    )
    clean_master = None
    cues = None
    if mode.needs_clean_master:
        service = ShortsService(settings)
        clean_master = master
        cues = service.load_caption_cues(paths, manifest)
    plan = build_plan(manifest, request)
    pipeline = ShortsPipeline(
        paths=paths, manifest=manifest, request=request, plan=plan,
        settings=settings, job_id=f"test-{mode.value}-{'-'.join(units)}",
        clean_master=clean_master, cue_sidecar=cues,
    )
    return await pipeline.run()


class TestSourceSelection:
    def test_a_native_short_is_cut_from_the_clean_master(self, prepared, settings) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        at = result.plan.total_duration_seconds / 2
        # Centre of the picture: the clean master's colour, not the export's.
        assert close_to(
            sample_pixel(result.artifacts.video, at, 540, 960, settings), MASTER_COLOUR[1]
        )

    def test_a_legacy_short_is_still_cut_from_the_captioned_export(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SOURCE_BURNED_IN)
        )
        at = result.plan.total_duration_seconds / 2
        assert close_to(
            sample_pixel(result.artifacts.video, at, 540, 960, settings), EXPORT_COLOUR[1]
        )

    def test_captions_off_also_uses_the_clean_master(self, prepared, settings) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.OFF)
        )
        at = result.plan.total_duration_seconds / 2
        assert close_to(
            sample_pixel(result.artifacts.video, at, 540, 960, settings), MASTER_COLOUR[1]
        )

    def test_audio_comes_straight_from_the_clean_master_mix(self, prepared, settings) -> None:  # noqa: ANN001
        """Nothing re-mixes narration or music in a Shorts job."""
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        volume = measure_mean_volume(result.artifacts.video, settings=settings)
        assert volume is not None and volume > -60.0

    def test_selection_semantics_are_unchanged(self, prepared, settings) -> None:  # noqa: ANN001
        """Adjacent sections still merge into one contiguous cut."""
        result = asyncio.run(
            build(
                prepared, settings, "scene-1", "scene-2",
                mode=ShortCaptionMode.SHORTS_NATIVE,
            )
        )
        assert len(result.plan.groups) == 1
        assert result.plan.groups[0].preserved_transitions == 1


class TestOutputIntegrity:
    def test_the_output_is_still_a_valid_vertical_short(self, prepared, settings) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        info = probe_video(result.artifacts.video, settings=settings)

        assert (info.width, info.height) == (1080, 1920)
        assert info.codec == "h264"
        assert info.pix_fmt == "yuv420p"
        assert info.avg_frame_rate == f"{FPS}/1"
        assert info.r_frame_rate == f"{FPS}/1"
        assert info.audio_codec == "aac"
        assert info.audio_sample_rate == 48_000
        assert info.duration_seconds == pytest.approx(
            result.plan.total_duration_seconds, abs=0.35
        )
        assert result.validation.passed

    def test_frame_intervals_stay_constant_with_captions_on(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        named = {a.name: a for a in result.validation.assertions}
        assert named["measured frame intervals"].passed

    def test_no_stretch_or_crop_regression(self, prepared, settings) -> None:  # noqa: ANN001
        """Captions must not change how the picture is fitted."""
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        geometry = result.validation.geometry
        assert (geometry.inner_width, geometry.inner_height) == (1080, 608)
        assert geometry.offset_x == 0 and geometry.offset_y == 656

        video = result.artifacts.video
        at = result.plan.total_duration_seconds / 2
        # Black above the picture, picture in the middle.
        assert close_to(sample_pixel(video, at, 20, 20, settings), (0, 0, 0), tol=12)
        assert close_to(sample_pixel(video, at, 540, 960, settings), MASTER_COLOUR[1])

    def test_a_multi_cut_native_short_keeps_its_duration(self, prepared, settings) -> None:  # noqa: ANN001
        """Looped caption stills must not run the output past the plan."""
        entry_two = None
        result = asyncio.run(
            build(
                prepared, settings, "scene-2", "scene-1",
                mode=ShortCaptionMode.SHORTS_NATIVE,
            )
        )
        del entry_two
        assert len(result.plan.groups) == 2
        info = probe_video(result.artifacts.video, settings=settings)
        assert info.duration_seconds == pytest.approx(
            result.plan.total_duration_seconds, abs=0.4
        )


class TestCaptionPixels:
    """Captions are drawn on the canvas, below the picture, at the bottom."""

    def caption_time(self, result, prepared) -> float:  # noqa: ANN001
        """A moment when a caption is definitely on screen."""
        _, _, manifest, _ = prepared
        cues = result.short_manifest.captions
        assert cues is not None and cues.rendered_cue_count > 0
        # The first cue starts a fraction after its section's safe start, which
        # is the start of the cut, so a second in is comfortably inside it.
        return 1.0

    def band(self, video, at, settings) -> int:  # noqa: ANN001
        """Brightest value in the caption band: y from 1400 to 1540."""
        return brightest_in(
            video, at, x=0, y=1400, width=1080, height=140, settings=settings
        )

    def test_captions_are_drawn_in_the_bottom_band_and_only_then(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        with_captions = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        without = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.OFF)
        )
        at = self.caption_time(with_captions, prepared)

        assert self.band(without.artifacts.video, at, settings) < 20, (
            "with captions off that band is plain black"
        )
        assert self.band(with_captions.artifacts.video, at, settings) > 180, (
            "with captions on, white type is drawn there"
        )

    def test_the_band_is_below_the_16_by_9_picture(self, prepared, settings) -> None:  # noqa: ANN001
        """The whole point: captions live on the canvas, not over the film."""
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        geometry = fit_geometry(SOURCE_WIDTH, SOURCE_HEIGHT, 1080, 1920)
        picture_bottom = geometry.offset_y + geometry.inner_height
        assert picture_bottom == 1264

        style = resolve_caption_style(None)
        card = result.short_manifest.captions
        assert card.safe_bottom_inset == style.safe_bottom_inset
        # The band probed above starts below where the picture ends.
        assert 1400 > picture_bottom

        at = self.caption_time(result, prepared)
        # ...and the picture itself is untouched by the captions.
        assert close_to(
            sample_pixel(result.artifacts.video, at, 540, 960, settings), MASTER_COLOUR[1]
        )
        assert (
            brightest_in(
                result.artifacts.video, at,
                x=0, y=picture_bottom + 20, width=1080, height=100, settings=settings,
            )
            < 20
        ), "the gap between the picture and the captions stays black"

    def test_nothing_is_drawn_over_the_shorts_controls(self, prepared, settings) -> None:  # noqa: ANN001
        """The safe inset is what keeps captions clear of the player UI."""
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        at = self.caption_time(result, prepared)
        assert (
            brightest_in(
                result.artifacts.video, at,
                x=0, y=1560, width=1080, height=360, settings=settings,
            )
            < 20
        )

    def test_a_larger_preset_draws_a_taller_card(self, prepared, settings) -> None:  # noqa: ANN001
        standard = asyncio.run(
            build(
                prepared, settings, "scene-1",
                mode=ShortCaptionMode.SHORTS_NATIVE,
                preset=ShortCaptionPreset.COMPACT,
            )
        )
        large = asyncio.run(
            build(
                prepared, settings, "scene-1",
                mode=ShortCaptionMode.SHORTS_NATIVE,
                preset=ShortCaptionPreset.LARGE,
            )
        )
        assert (
            large.short_manifest.captions.fitted_font_size
            > standard.short_manifest.captions.fitted_font_size
        )
        assert large.short_manifest.cache_key != standard.short_manifest.cache_key


class TestDenseTrack:
    """Above the inline cap, cards are baked into one alpha track first."""

    def test_a_dense_track_is_precomposed_and_still_draws(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        slug, paths, manifest, master = prepared
        # More cues in the cut than MAX_INLINE_CAPTION_OVERLAYS, so the compose
        # graph would otherwise gain one input and two filters per cue.
        dense = cues_for(manifest.entries, per_section=7)
        package, sidecar = make_shorts_source(master, manifest=manifest, cues=dense)
        manifest.shorts_source = write_shorts_source(package, sidecar, master)
        write_manifest(manifest, paths.exports / f"{slug}_v01.mp4")

        result = asyncio.run(
            build(
                prepared, settings, "scene-1", "scene-2",
                mode=ShortCaptionMode.SHORTS_NATIVE,
            )
        )

        provenance = result.short_manifest.captions
        assert provenance.rendered_cue_count > MAX_INLINE_CAPTION_OVERLAYS
        assert provenance.precomposed is True, "a dense track must be pre-composited"

        # The track is scratch: it lives in the rebuildable cache, not exports.
        tracks = list((paths.shorts_cache / "caption-tracks").glob("*.mov"))
        assert tracks, "the alpha track is cached for reuse"
        assert not list(paths.shorts_exports.glob("*.mov"))

        # ...and the result still has captions in the right band. The first
        # group starts at 0, so a source cue's midpoint is also an output time.
        third = dense[2]
        at = (third.start_seconds + third.end_seconds) / 2
        assert (
            brightest_in(
                result.artifacts.video, at,
                x=0, y=1400, width=1080, height=140, settings=settings,
            )
            > 180
        )
        info = probe_video(result.artifacts.video, settings=settings)
        assert (info.width, info.height) == (1080, 1920)
        assert result.validation.passed


class TestManifestAndArtifacts:
    def test_the_short_manifest_records_the_caption_provenance(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        written = json.loads(result.artifacts.manifest.read_text("utf-8"))

        assert written["captionMode"] == "shorts-native"
        assert written["captionStyle"]["preset"] == "standard"
        captions = written["captions"]
        assert captions["mode"] == "shorts-native"
        assert captions["renderedCueCount"] > 0
        assert captions["cleanMaster"].endswith("-clean.mp4")
        assert len(captions["cleanMasterSha256"]) == 64
        assert captions["cueSidecar"].endswith("-shorts-cues.json")
        assert captions["cueSchemaVersion"] == 1
        assert captions["safeBottomInset"] == resolve_caption_style(None).safe_bottom_inset

    def test_a_legacy_short_records_the_legacy_mode(self, prepared, settings) -> None:  # noqa: ANN001
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SOURCE_BURNED_IN)
        )
        written = json.loads(result.artifacts.manifest.read_text("utf-8"))
        assert written["captionMode"] == "source-burned-in"
        assert written["captionStyle"] is None
        assert written["captions"]["cleanMaster"] is None

    def test_the_usual_artifacts_are_still_produced(self, prepared, settings) -> None:  # noqa: ANN001
        slug, paths, _, _ = prepared
        result = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        assert result.artifacts.video.parent == paths.shorts_exports
        assert result.artifacts.manifest.is_file()
        assert result.artifacts.log.is_file()
        assert not list(paths.shorts_exports.glob("*.partial*"))
        # Caption scratch stays in the rebuildable cache, out of the user's way:
        # the finished Short is an MP4, a manifest and a log, exactly as before.
        assert not list(paths.shorts_exports.glob("*.mov"))
        assert not list(paths.shorts_exports.glob("*.png"))
        assert {p.suffix for p in paths.shorts_exports.iterdir()} == {".mp4", ".json", ".log"}

    def test_the_long_render_and_its_clean_master_are_read_only(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        slug, paths, _, _ = prepared

        def stamps(directory):  # noqa: ANN001, ANN202
            return {
                p.relative_to(paths.exports).as_posix(): p.stat().st_mtime_ns
                for p in directory.rglob("*") if p.is_file()
            }

        before = stamps(paths.exports)
        asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        after = stamps(paths.exports)

        # The Short itself is new; nothing that existed before was touched.
        for name, stamp in before.items():
            assert after.get(name) == stamp, f"{name} was modified during a Shorts render"

    def test_identical_requests_reuse_the_same_cache_key(self, prepared, settings) -> None:  # noqa: ANN001
        first = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        second = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        assert first.plan.cache_key == second.plan.cache_key
        assert first.artifacts.video.name == second.artifacts.video.name

    def test_caption_mode_produces_a_different_short(self, prepared, settings) -> None:  # noqa: ANN001
        native = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SHORTS_NATIVE)
        )
        legacy = asyncio.run(
            build(prepared, settings, "scene-1", mode=ShortCaptionMode.SOURCE_BURNED_IN)
        )
        assert native.plan.cache_key != legacy.plan.cache_key
        assert native.artifacts.video != legacy.artifacts.video
        assert native.artifacts.video.is_file() and legacy.artifacts.video.is_file()


class TestRefusal:
    def test_the_pipeline_refuses_native_captions_without_a_clean_master(
        self, prepared, settings
    ) -> None:  # noqa: ANN001
        """No path exists that would silently cut the captioned export instead."""
        slug, paths, manifest, _ = prepared
        request = ShortRequest(
            source_render_id=manifest.render_job_id,
            segments=request_for("scene-1").segments,
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
        )
        plan = build_plan(manifest, request)

        with pytest.raises(AppError) as exc:
            ShortsPipeline(
                paths=paths, manifest=manifest, request=request, plan=plan,
                settings=settings, job_id="test-refusal",
                clean_master=None, cue_sidecar=None,
            )
        assert exc.value.code is ErrorCode.SHORT_CAPTIONS_UNAVAILABLE

    def test_a_failed_run_publishes_nothing(self, prepared, settings) -> None:  # noqa: ANN001
        slug, paths, manifest, master = prepared
        # Corrupt the clean master after preflight would have passed.
        cues = ShortsService(settings).load_caption_cues(paths, manifest)
        master.write_bytes(b"not a video any more")

        request = ShortRequest(
            source_render_id=manifest.render_job_id,
            segments=request_for("scene-1").segments,
            caption_mode=ShortCaptionMode.SHORTS_NATIVE,
        )
        plan = build_plan(manifest, request)
        pipeline = ShortsPipeline(
            paths=paths, manifest=manifest, request=request, plan=plan,
            settings=settings, job_id="test-corrupt",
            clean_master=master, cue_sidecar=cues,
        )

        with pytest.raises(AppError) as exc:
            asyncio.run(pipeline.run())

        assert exc.value.code is ErrorCode.SHORT_CLEAN_SOURCE_STALE
        assert not list(paths.shorts_exports.glob("*.mp4"))
        assert not (paths.shorts_cache / "work" / "test-corrupt").exists()
