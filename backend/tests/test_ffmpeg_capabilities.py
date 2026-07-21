"""FFmpeg capability probing and command-safety guarantees."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.render.ffmpeg import (
    OPTIONAL_FILTERS,
    REQUIRED_ENCODERS,
    REQUIRED_FILTERS,
    Capabilities,
    FFmpegRunner,
    base_output_args,
)
from tests.conftest import requires_ffmpeg


def make_caps(filters: set[str], encoders: set[str]) -> Capabilities:
    return Capabilities(
        ffmpeg_path="/x/ffmpeg",
        ffprobe_path="/x/ffprobe",
        ffmpeg_version="ffmpeg version test",
        ffprobe_version="ffprobe version test",
        configuration="",
        filters=frozenset(filters),
        encoders=frozenset(encoders),
    )


@requires_ffmpeg
class TestRealBinary:
    def test_probe_finds_filters_and_encoders(self) -> None:
        caps = FFmpegRunner().probe_capabilities()
        # Sanity: the parser is not silently returning an empty set.
        assert len(caps.filters) > 50
        assert len(caps.encoders) > 20
        assert "scale" in caps.filters
        assert "libx264" in caps.encoders

    def test_all_required_capabilities_present_on_this_machine(self) -> None:
        caps = FFmpegRunner().probe_capabilities()
        assert caps.missing_required_filters == []
        assert caps.missing_required_encoders == []
        assert caps.is_usable

    def test_probe_is_honest_about_drawtext(self) -> None:
        """Whatever the answer, it must match what the binary really does."""
        caps = FFmpegRunner().probe_capabilities()
        runner = FFmpegRunner()
        result = runner._run_sync(  # noqa: SLF001 - deliberately testing the real behaviour
            [
                caps.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                "-vf", "drawtext=text='x':fontsize=12",
                "-frames:v", "1", "-f", "null", "-",
            ]
        )
        drawtext_actually_works = result.ok
        assert caps.has_drawtext == drawtext_actually_works

    def test_pillow_is_always_the_text_engine(self) -> None:
        assert FFmpegRunner().probe_capabilities().text_engine == "pillow"


class TestCapabilityLogic:
    def test_missing_required_filter_makes_build_unusable(self) -> None:
        caps = make_caps(set(REQUIRED_FILTERS) - {"zoompan"}, set(REQUIRED_ENCODERS))
        assert caps.missing_required_filters == ["zoompan"]
        assert not caps.is_usable

    def test_missing_encoder_makes_build_unusable(self) -> None:
        caps = make_caps(set(REQUIRED_FILTERS), {"aac"})
        assert caps.missing_required_encoders == ["libx264"]
        assert not caps.is_usable

    def test_build_without_drawtext_is_still_usable(self) -> None:
        """The whole architecture rests on this being true."""
        caps = make_caps(set(REQUIRED_FILTERS), set(REQUIRED_ENCODERS))
        assert not caps.has_drawtext
        assert not caps.has_libass
        assert caps.is_usable

    def test_notes_explain_each_missing_optional_capability(self) -> None:
        caps = make_caps(set(REQUIRED_FILTERS), set(REQUIRED_ENCODERS))
        notes = " ".join(caps.notes())
        assert "drawtext" in notes
        assert "Pillow" in notes
        assert "xfade" in notes
        assert "sidechaincompress" in notes
        assert "loudnorm" in notes

    def test_no_notes_when_everything_is_available(self) -> None:
        caps = make_caps(set(REQUIRED_FILTERS) | set(OPTIONAL_FILTERS), set(REQUIRED_ENCODERS))
        assert caps.notes() == []


class TestOutputArgs:
    def test_constant_frame_rate_flags(self) -> None:
        args = base_output_args(fps=60)
        assert args == ["-r", "60", "-fps_mode", "cfr"]

    def test_does_not_use_deprecated_vsync(self) -> None:
        # FFmpeg 8 warns on every command if -vsync is passed alongside -fps_mode.
        assert "-vsync" not in base_output_args(fps=60)


class TestToolResolution:
    def test_missing_tool_raises_actionable_error(self, settings, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.errors import AppError

        # Patch on the class: pydantic models reject setting undeclared attributes.
        monkeypatch.setattr(type(settings), "resolve_tool", lambda self, name: None)
        with pytest.raises(AppError) as exc_info:
            settings.require_tool("ffmpeg")
        assert exc_info.value.code.value == "ffmpeg_not_found"
        assert "brew install ffmpeg" in exc_info.value.suggestion


class TestProgressParsing:
    """FFmpeg reports progress two ways; parsing only one froze the encode bar."""

    def test_parses_human_readable_stderr(self) -> None:
        from app.render.ffmpeg import _parse_progress_seconds

        line = "frame=  247 fps= 82 q=23.0 size= 512KiB time=00:00:04.11 bitrate=1020.2kbits/s"
        assert _parse_progress_seconds(line) == pytest.approx(4.11, abs=0.01)

    def test_parses_machine_readable_progress(self) -> None:
        from app.render.ffmpeg import _parse_progress_seconds

        assert _parse_progress_seconds("out_time_ms=2933333") == pytest.approx(2.933, abs=0.001)

    def test_parses_hours(self) -> None:
        from app.render.ffmpeg import _parse_progress_seconds

        assert _parse_progress_seconds("time=01:02:03.50") == pytest.approx(3723.5, abs=0.01)

    def test_ignores_unrelated_lines(self) -> None:
        from app.render.ffmpeg import _parse_progress_seconds

        assert _parse_progress_seconds("[libx264 @ 0x7f] using SAR=1/1") is None
        assert _parse_progress_seconds("") is None

    @requires_ffmpeg
    def test_real_encode_reports_advancing_progress(self) -> None:
        """End to end: a real encode must move the bar, not sit at one value.

        This is the regression test for a bug where the encode phase — the
        longest single step of a render — showed a frozen progress bar because
        only the ``out_time_ms`` form was parsed, and FFmpeg does not emit it
        unless ``-progress`` is passed.
        """
        import asyncio
        import tempfile
        from pathlib import Path

        from app.render.ffmpeg import FFmpegRunner

        settings = get_settings()
        ffmpeg = settings.require_tool("ffmpeg")
        reported: list[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "progress.mp4"
            asyncio.run(
                FFmpegRunner(settings).run(
                    [
                        ffmpeg, "-hide_banner", "-nostdin", "-y",
                        # A short stats period so a brief clip still reports
                        # several updates, as a long render naturally would.
                        "-stats_period", "0.1",
                        "-f", "lavfi",
                        "-i", "testsrc=size=1280x720:duration=6:rate=60",
                        "-c:v", "libx264", "-preset", "slow", str(target),
                    ],
                    expected_duration=6.0,
                    on_progress=reported.append,
                    stage="progress-test",
                )
            )

        # How many updates arrive depends on machine speed, so the assertion is
        # about movement, not count: before the fix every value was identical.
        assert len(set(reported)) > 1, "progress never changed — the bar would look frozen"
        assert reported == sorted(reported), "progress went backwards"
        assert min(reported) < 0.5 < max(reported), "progress did not span the encode"
