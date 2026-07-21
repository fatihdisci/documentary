"""The render smoke test — the gate before the full pipeline is built.

Renders a real two-scene video with Ken Burns motion, a Pillow title overlay, a
caption, one transition, narration and background audio, then validates the
actual file. Exit code 0 from FFmpeg is never treated as success on its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.render.smoke import render_smoke_video
from app.timing.probe import frame_timestamps, measure_mean_volume, probe_video
from tests.conftest import requires_ffmpeg
from tests.factories import make_image_bytes, make_wav_bytes

pytestmark = [requires_ffmpeg, pytest.mark.slow]

SCENE_SECONDS = 3.0
TRANSITION_SECONDS = 0.6
EXPECTED_DURATION = SCENE_SECONDS * 2 - TRANSITION_SECONDS  # 5.4s


@pytest.fixture(scope="module")
def smoke_video(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    """Render once; every assertion below inspects the same real file."""
    import asyncio

    workdir = tmp_path_factory.mktemp("smoke")
    images = []
    for index in range(2):
        path = workdir / f"image-{index}.png"
        path.write_bytes(make_image_bytes(1920, 1080, seed=index * 4 + 1))
        images.append(path)

    narration = []
    for index, seconds in enumerate((2.4, 2.0)):
        path = workdir / f"narration-{index}.wav"
        path.write_bytes(make_wav_bytes(seconds, freq=200 + index * 80))
        narration.append(path)

    output = workdir / "smoke.mp4"
    result = asyncio.run(
        render_smoke_video(
            workdir=workdir,
            images=images,
            narration=narration,
            output=output,
            scene_seconds=SCENE_SECONDS,
            transition_seconds=TRANSITION_SECONDS,
        )
    )
    return result, output


class TestOutputIsValid:
    def test_file_exists_and_is_substantial(self, smoke_video) -> None:  # noqa: ANN001
        _, output = smoke_video
        assert output.is_file()
        assert output.stat().st_size > 100_000, "suspiciously small for 5s of 1080p"

    def test_resolution(self, smoke_video) -> None:  # noqa: ANN001
        info = probe_video(smoke_video[1])
        assert (info.width, info.height) == (1920, 1080)

    def test_video_codec_and_pixel_format(self, smoke_video) -> None:  # noqa: ANN001
        info = probe_video(smoke_video[1])
        assert info.codec == "h264"
        assert info.pix_fmt == "yuv420p", "yuv420p is required for broad compatibility"

    def test_constant_60_fps(self, smoke_video) -> None:  # noqa: ANN001
        info = probe_video(smoke_video[1])
        assert info.avg_frame_rate == "60/1"
        assert info.r_frame_rate == "60/1"

    def test_frame_intervals_really_are_one_sixtieth(self, smoke_video) -> None:  # noqa: ANN001
        """Corroborates CFR by measurement, not by trusting the container."""
        timestamps = frame_timestamps(smoke_video[1], seconds=4.0)
        assert len(timestamps) > 100

        intervals = [
            timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))
        ]
        expected = 1 / 60
        deviations = [i for i in intervals if abs(i - expected) > 0.002]
        assert not deviations, f"{len(deviations)} frame intervals are not 1/60s"

    def test_frame_count_matches_duration_when_reported(self, smoke_video) -> None:  # noqa: ANN001
        """nb_frames is checked only when present, with a small tolerance."""
        info = probe_video(smoke_video[1])
        if info.nb_frames is None:
            pytest.skip("this container did not report nb_frames")
        expected = round(info.duration_seconds * 60)
        assert abs(info.nb_frames - expected) <= 3

    def test_duration_matches_the_timeline(self, smoke_video) -> None:  # noqa: ANN001
        """Total = both scenes minus the transition overlap."""
        info = probe_video(smoke_video[1])
        assert info.duration_seconds == pytest.approx(EXPECTED_DURATION, abs=0.15)

    def test_audio_stream_present_and_correct(self, smoke_video) -> None:  # noqa: ANN001
        info = probe_video(smoke_video[1])
        assert info.has_audio
        assert info.audio_codec == "aac"
        assert info.audio_sample_rate == 48_000

    def test_audio_is_not_silence(self, smoke_video) -> None:  # noqa: ANN001
        """A render with a silent audio track is a failed render."""
        mean_volume = measure_mean_volume(smoke_video[1])
        assert mean_volume is not None
        assert mean_volume > -60.0, f"audio is effectively silent ({mean_volume} dB)"

    def test_audio_does_not_clip(self, smoke_video) -> None:  # noqa: ANN001
        result = subprocess.run(  # noqa: S603
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(smoke_video[1]),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, check=False,
        )
        peaks = [
            float(line.split("max_volume:")[1].strip().split()[0])
            for line in result.stderr.splitlines()
            if "max_volume:" in line
        ]
        assert peaks and peaks[0] <= 0.0, f"audio peaks at {peaks} dBFS"

    def test_faststart_is_enabled(self, smoke_video) -> None:  # noqa: ANN001
        """moov before mdat, so the file starts playing before it fully loads."""
        head = smoke_video[1].read_bytes()[:400_000]
        moov, mdat = head.find(b"moov"), head.find(b"mdat")
        assert moov != -1 and mdat != -1
        assert moov < mdat, "moov atom is not at the front (faststart missing)"


class TestPictureContent:
    def _frame(self, video: Path, at: float, tmp: Path) -> Path:
        target = tmp / f"frame-{at}.png"
        subprocess.run(  # noqa: S603
            ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video),
             "-frames:v", "1", "-y", str(target)],
            check=True, capture_output=True,
        )
        return target

    def test_no_black_frames_anywhere(self, smoke_video, tmp_path: Path) -> None:  # noqa: ANN001
        """Especially at the transition, where a bad xfade shows black."""
        from PIL import Image, ImageStat

        _, video = smoke_video
        for at in (0.1, 1.5, 2.4, 2.7, 3.0, 4.5, 5.2):
            frame = self._frame(video, at, tmp_path)
            with Image.open(frame) as image:
                mean = ImageStat.Stat(image.convert("L")).mean[0]
                assert mean > 8, f"frame at {at}s is essentially black (mean luma {mean:.1f})"

    def test_the_transition_actually_blends(self, smoke_video, tmp_path: Path) -> None:  # noqa: ANN001
        """Mid-transition must differ from both neighbours — proof of a dissolve."""
        from PIL import Image, ImageChops, ImageStat

        _, video = smoke_video
        before = self._frame(video, 2.0, tmp_path)
        middle = self._frame(video, 2.7, tmp_path)
        after = self._frame(video, 3.4, tmp_path)

        def difference(a: Path, b: Path) -> float:
            with Image.open(a) as ia, Image.open(b) as ib:
                diff = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB"))
                return ImageStat.Stat(diff.convert("L")).mean[0]

        assert difference(before, middle) > 3, "no change into the transition"
        assert difference(middle, after) > 3, "no change out of the transition"

    def test_the_picture_moves(self, smoke_video, tmp_path: Path) -> None:  # noqa: ANN001
        """Ken Burns must actually animate, not hold a still."""
        from PIL import Image, ImageChops, ImageStat

        _, video = smoke_video
        early = self._frame(video, 0.3, tmp_path)
        late = self._frame(video, 2.2, tmp_path)

        with Image.open(early) as a, Image.open(late) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
            mean = ImageStat.Stat(diff.convert("L")).mean[0]
        assert mean > 2, f"the picture barely changed within one scene (mean diff {mean:.2f})"

    def test_text_is_composited(self, smoke_video, tmp_path: Path) -> None:  # noqa: ANN001
        """The lower third must be visibly brighter where white text sits."""
        from PIL import Image

        _, video = smoke_video
        frame = self._frame(video, 1.6, tmp_path)
        with Image.open(frame) as image:
            # The title sits around y=840-930, x=80-800 at 1080p.
            title_area = image.convert("L").crop((80, 840, 800, 930))
            brightest = title_area.getextrema()[1]
        assert brightest > 200, (
            "no bright pixels where the title should be — the Pillow overlay did not composite"
        )


class TestGateSummary:
    def test_reported_properties_match_the_file(self, smoke_video) -> None:  # noqa: ANN001
        """The function's own report must not disagree with the real file."""
        result, output = smoke_video
        info = probe_video(output)
        assert result.width == info.width
        assert result.height == info.height
        assert result.fps == pytest.approx(60.0)
        assert result.has_audio is info.has_audio
        assert result.duration_seconds == pytest.approx(info.duration_seconds, abs=0.01)
