"""Safe FFmpeg/ffprobe invocation.

Two rules hold everywhere in this module and everything that uses it:

1. Commands are argument *lists*, never shell strings. No user text is ever
   interpolated into a shell.
2. No user text is ever interpolated into a filtergraph either. Text becomes a
   PNG (see render/text.py); paths are passed as ``-i`` arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, EnvironmentError_, ErrorCode, RenderError

logger = logging.getLogger("evb.ffmpeg")

#: Filters this application needs. Probed once and reported in Diagnostics.
REQUIRED_FILTERS: tuple[str, ...] = (
    "scale",
    "zoompan",
    "overlay",
    "fade",
    "format",
    "setsar",
    "fps",
    "trim",
    "setpts",
    "adelay",
    "amix",
    "afade",
    "apad",
    "atrim",
    "asetpts",
    "volume",
    "anull",
)

#: Filters that improve the result but that we have working fallbacks for.
OPTIONAL_FILTERS: tuple[str, ...] = (
    "xfade",             # transitions; without it we hard-cut
    "sidechaincompress", # music ducking; without it we use a static level
    "loudnorm",          # loudness normalization; without it we use volume
    "aloop",             # music looping
    "gblur",
    "drawtext",          # NOT used — probed only so Diagnostics can explain why
    "subtitles",         # NOT used — same
    "ass",               # NOT used — same
)

REQUIRED_ENCODERS: tuple[str, ...] = ("libx264", "aac")
OPTIONAL_ENCODERS: tuple[str, ...] = ("h264_videotoolbox", "prores_ks", "prores", "aac_at")

_PROGRESS_TIME = re.compile(r"out_time_ms=(\d+)")
_PROGRESS_FRAME = re.compile(r"^frame=(\d+)$", re.MULTILINE)


@dataclass(frozen=True)
class Capabilities:
    """What this FFmpeg build can actually do.

    Computed by probing the binary, never assumed. ``text_engine`` records the
    consequence that matters most: this project renders all text with Pillow, so
    a build without libfreetype/libass is fully supported.
    """

    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_version: str
    ffprobe_version: str
    configuration: str
    filters: frozenset[str]
    encoders: frozenset[str]

    @property
    def missing_required_filters(self) -> list[str]:
        return sorted(f for f in REQUIRED_FILTERS if f not in self.filters)

    @property
    def missing_required_encoders(self) -> list[str]:
        return sorted(e for e in REQUIRED_ENCODERS if e not in self.encoders)

    @property
    def has_drawtext(self) -> bool:
        return "drawtext" in self.filters

    @property
    def has_libass(self) -> bool:
        return "subtitles" in self.filters or "ass" in self.filters

    @property
    def has_xfade(self) -> bool:
        return "xfade" in self.filters

    @property
    def has_sidechain(self) -> bool:
        return "sidechaincompress" in self.filters

    @property
    def has_loudnorm(self) -> bool:
        return "loudnorm" in self.filters

    @property
    def has_prores(self) -> bool:
        return "prores_ks" in self.encoders or "prores" in self.encoders

    @property
    def has_videotoolbox(self) -> bool:
        return "h264_videotoolbox" in self.encoders

    @property
    def text_engine(self) -> str:
        return "pillow"

    @property
    def is_usable(self) -> bool:
        return not self.missing_required_filters and not self.missing_required_encoders

    def notes(self) -> list[str]:
        """Human-readable explanations shown on the Diagnostics page."""
        out: list[str] = []
        if not self.has_drawtext:
            out.append(
                "This FFmpeg build has no 'drawtext' filter (compiled without libfreetype). "
                "That is fine: all text is rendered by Pillow into transparent PNGs and "
                "composited with 'overlay', which looks identical on every machine."
            )
        if not self.has_libass:
            out.append(
                "No 'subtitles'/'ass' filter (compiled without libass). Burned-in subtitles "
                "use the same Pillow overlay path. External .srt export is unaffected."
            )
        if not self.has_xfade:
            out.append(
                "No 'xfade' filter — transitions will fall back to hard cuts. Install a "
                "fuller FFmpeg build to get dissolves."
            )
        if not self.has_sidechain:
            out.append(
                "No 'sidechaincompress' filter — music ducking falls back to a fixed lower "
                "music level under narration."
            )
        if not self.has_loudnorm:
            out.append(
                "No 'loudnorm' filter — output loudness is set with a fixed gain instead of "
                "EBU R128 normalization."
            )
        return out


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def pretty_command(self) -> str:
        return " ".join(shlex.quote(a) for a in self.args)


class CancelledRender(Exception):
    """Raised inside the pipeline when a job is cancelled mid-command."""


@dataclass
class FFmpegRunner:
    """Runs ffmpeg/ffprobe. One instance per request or per render job."""

    settings: Settings = field(default_factory=get_settings)

    # --- Discovery --------------------------------------------------------

    def probe_capabilities(self) -> Capabilities:
        ffmpeg = self.settings.require_tool("ffmpeg")
        ffprobe = self.settings.require_tool("ffprobe")

        version_out = self._run_sync([ffmpeg, "-hide_banner", "-version"])
        ffprobe_out = self._run_sync([ffprobe, "-hide_banner", "-version"])
        filters_out = self._run_sync([ffmpeg, "-hide_banner", "-filters"])
        encoders_out = self._run_sync([ffmpeg, "-hide_banner", "-encoders"])

        return Capabilities(
            ffmpeg_path=ffmpeg,
            ffprobe_path=ffprobe,
            ffmpeg_version=_first_version_line(version_out.stdout),
            ffprobe_version=_first_version_line(ffprobe_out.stdout),
            configuration=_configuration_line(version_out.stdout),
            filters=frozenset(_parse_listing(filters_out.stdout, name_column=1)),
            encoders=frozenset(_parse_listing(encoders_out.stdout, name_column=1)),
        )

    # --- ffprobe ----------------------------------------------------------

    def probe_json(self, path: Path, *, extra: Sequence[str] = ()) -> dict:
        """Run ffprobe and return parsed JSON, or raise a specific AppError."""
        ffprobe = self.settings.require_tool("ffprobe")
        if not path.exists():
            raise AppError(
                ErrorCode.MISSING_IMAGE if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                else ErrorCode.MISSING_AUDIO,
                f"File not found: {path.name}",
                details=str(path),
            )
        args = [
            ffprobe, "-hide_banner", "-loglevel", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            *extra,
            str(path),
        ]
        result = self._run_sync(args)
        if not result.ok:
            raise AppError(
                ErrorCode.CORRUPT_AUDIO,
                f"FFmpeg could not read '{path.name}'.",
                details=f"{result.pretty_command()}\n\n{result.stderr.strip()}",
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RenderError(
                ErrorCode.FFMPEG_FAILED,
                f"ffprobe returned unreadable output for '{path.name}'.",
                details=f"{result.stdout[:2000]}\n\n{exc}",
            ) from exc

    # --- Execution --------------------------------------------------------

    def _run_sync(self, args: Sequence[str], *, timeout: float = 60.0) -> CommandResult:
        try:
            completed = subprocess.run(  # noqa: S603 - argument list, never a shell
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise EnvironmentError_(
                ErrorCode.FFMPEG_NOT_FOUND,
                f"Could not execute '{args[0]}'.",
                details=str(exc),
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderError(
                ErrorCode.FFMPEG_FAILED,
                f"'{Path(args[0]).name}' timed out after {timeout:.0f}s.",
                details=" ".join(shlex.quote(a) for a in args),
            ) from exc
        return CommandResult(list(args), completed.returncode, completed.stdout, completed.stderr)

    async def run(
        self,
        args: Sequence[str],
        *,
        expected_duration: float | None = None,
        on_progress: Callable[[float], None] | None = None,
        log_sink: Callable[[str], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        stage: str = "ffmpeg",
    ) -> CommandResult:
        """Run FFmpeg asynchronously with progress reporting and cancellation.

        ``on_progress`` receives a 0.0-1.0 fraction whenever FFmpeg reports a new
        output timestamp. Requires ``expected_duration`` to be meaningful.
        """
        arg_list = [str(a) for a in args]
        logger.debug("[%s] %s", stage, " ".join(shlex.quote(a) for a in arg_list))
        if log_sink:
            log_sink(f"$ {' '.join(shlex.quote(a) for a in arg_list)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *arg_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise EnvironmentError_(
                ErrorCode.FFMPEG_NOT_FOUND,
                f"Could not execute '{arg_list[0]}'.",
                details=str(exc),
            ) from exc

        stderr_chunks: list[str] = []

        async def pump_stderr() -> None:
            assert process.stderr is not None
            while True:
                raw = await process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                stderr_chunks.append(line)
                # Keep memory bounded on very chatty runs.
                if len(stderr_chunks) > 4000:
                    del stderr_chunks[:1000]
                if log_sink:
                    log_sink(line)
                if on_progress and expected_duration:
                    match = _PROGRESS_TIME.search(line)
                    if match:
                        seconds = int(match.group(1)) / 1_000_000
                        on_progress(min(1.0, seconds / expected_duration))

        async def pump_stdout() -> str:
            assert process.stdout is not None
            data = await process.stdout.read()
            return data.decode("utf-8", "replace")

        async def watch_cancel() -> None:
            if cancel_event is None:
                return
            await cancel_event.wait()
            if process.returncode is None:
                logger.info("[%s] cancelling: terminating ffmpeg pid=%s", stage, process.pid)
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()

        cancel_task = asyncio.create_task(watch_cancel())
        try:
            stdout_text, _ = await asyncio.gather(pump_stdout(), pump_stderr())
            returncode = await process.wait()
        finally:
            cancel_task.cancel()

        if cancel_event is not None and cancel_event.is_set():
            raise CancelledRender(stage)

        result = CommandResult(arg_list, returncode, stdout_text, "\n".join(stderr_chunks))
        if not result.ok:
            raise RenderError(
                ErrorCode.FFMPEG_FAILED,
                f"FFmpeg failed during '{stage}' (exit code {returncode}).",
                details=(
                    f"{result.pretty_command()}\n\n"
                    f"--- last 40 lines of stderr ---\n"
                    + "\n".join(stderr_chunks[-40:])
                ),
            )
        return result


# --- module-level helpers -------------------------------------------------


def _first_version_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(("ffmpeg version", "ffprobe version")):
            return line.strip()
    return text.splitlines()[0].strip() if text.strip() else "unknown"


def _configuration_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("configuration:"):
            return line.strip()
    return ""


def _parse_listing(text: str, *, name_column: int) -> Iterable[str]:
    """Parse ``ffmpeg -filters`` / ``-encoders`` table output into names.

    Both listings share the shape ``<flags> <name> <io> <description>`` after a
    header terminated by a line of dashes.
    """
    started = False
    for line in text.splitlines():
        if not started:
            if set(line.strip()) == {"-"} or line.strip().startswith("------"):
                started = True
            continue
        parts = line.split()
        if len(parts) > name_column:
            yield parts[name_column]


def base_output_args(*, fps: int) -> list[str]:
    """Arguments every video output shares, guaranteeing constant frame rate.

    ``-vsync`` is deliberately absent: FFmpeg 8 deprecates it in favour of
    ``-fps_mode``, and passing both prints a warning on every single command.
    """
    return ["-r", str(fps), "-fps_mode", "cfr"]
