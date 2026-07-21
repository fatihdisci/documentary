"""Edge TTS provider — the free default. Requires network, no API key."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.errors import AppError, ErrorCode
from app.tts.base import (
    ProviderStatus,
    SynthesisRequest,
    SynthesisResult,
    Voice,
    WordTiming,
)
from app.tts.pronunciation import apply_pronunciation, sanitize_for_tts

logger = logging.getLogger("evb.tts.edge")

MAX_ATTEMPTS = 3
ATTEMPT_TIMEOUT_SECONDS = 120.0
#: Cached across calls: the voice list rarely changes and the request is slow.
_voice_cache: list[Voice] | None = None


class EdgeTTSProvider:
    name = "edge"

    def status(self) -> ProviderStatus:
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            return ProviderStatus(
                name=self.name,
                available=False,
                message=f"edge-tts is not installed ({exc}).",
                supports_rate=True,
                supports_pitch=True,
                supports_word_timings=True,
            )
        return ProviderStatus(
            name=self.name,
            available=True,
            message="Free, no API key. Requires an internet connection.",
            supports_rate=True,
            supports_pitch=True,
            supports_word_timings=True,
            offline=False,
        )

    async def list_voices(self) -> list[Voice]:
        global _voice_cache
        if _voice_cache is not None:
            return _voice_cache

        try:
            import edge_tts

            raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=30.0)
        except asyncio.TimeoutError as exc:
            raise AppError(
                ErrorCode.TTS_TIMEOUT,
                "Timed out fetching the Edge TTS voice list.",
                details=str(exc),
                suggestion="Check your internet connection, or upload narration audio instead.",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - any transport failure is the same story
            raise AppError(
                ErrorCode.TTS_PROVIDER_UNAVAILABLE,
                "Could not reach the Edge TTS service to list voices.",
                details=f"{type(exc).__name__}: {exc}",
            ) from exc

        voices = [
            Voice(
                id=str(entry.get("ShortName", "")),
                name=str(entry.get("FriendlyName") or entry.get("ShortName", "")),
                locale=str(entry.get("Locale", "")),
                gender=str(entry.get("Gender", "")),
                description=", ".join(
                    (entry.get("VoiceTag") or {}).get("VoicePersonalities", []) or []
                ),
            )
            for entry in raw
            if entry.get("ShortName")
        ]
        voices.sort(key=lambda v: (not v.locale.startswith("en"), v.locale, v.name))
        _voice_cache = voices
        return voices

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import edge_tts

        text = sanitize_for_tts(apply_pronunciation(request.text, request.pronunciation))
        if not text:
            raise AppError(
                ErrorCode.MISSING_NARRATION,
                "There is no narration text to synthesize.",
                suggestion="Add narration to this scene, or disable it.",
            )

        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    self._attempt(edge_tts, text, request),
                    timeout=ATTEMPT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("edge-tts attempt %d timed out", attempt)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("edge-tts attempt %d failed: %s", attempt, exc)
                if _is_auth_error(exc):
                    break  # retrying a rejected request will not help
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(1.5 * attempt)  # linear backoff

        request.output_path.unlink(missing_ok=True)
        if isinstance(last_error, asyncio.TimeoutError):
            raise AppError(
                ErrorCode.TTS_TIMEOUT,
                f"Edge TTS timed out after {MAX_ATTEMPTS} attempts.",
                details=str(last_error),
                suggestion=(
                    "Check your connection and retry. You can also upload narration audio "
                    "for this scene and render entirely offline."
                ),
            )
        raise AppError(
            ErrorCode.TTS_FAILED,
            f"Edge TTS failed after {MAX_ATTEMPTS} attempts.",
            details=f"{type(last_error).__name__}: {last_error}",
            suggestion=(
                "Check your internet connection. If it persists, switch the provider to "
                "'imported' and upload narration audio per scene."
            ),
        )

    async def _attempt(self, edge_tts, text: str, request: SynthesisRequest) -> SynthesisResult:  # noqa: ANN001
        communicate = edge_tts.Communicate(
            text,
            request.voice,
            rate=_rate_string(request.rate),
            pitch=_pitch_string(request.pitch),
            # edge-tts defaults to SentenceBoundary; word granularity gives the
            # subtitle engine much finer alignment. Both are handled below in
            # case a future version changes the default again.
            boundary="WordBoundary",
        )

        audio = bytearray()
        word_timings: list[WordTiming] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] in {"WordBoundary", "SentenceBoundary"}:
                # Edge reports offsets in 100-nanosecond ticks.
                start = chunk["offset"] / 10_000_000
                duration = chunk["duration"] / 10_000_000
                word_timings.append(
                    WordTiming(
                        word=str(chunk.get("text", "")),
                        start_seconds=start,
                        end_seconds=start + duration,
                    )
                )

        if not audio:
            raise RuntimeError("Edge TTS returned no audio data")

        request.output_path.write_bytes(bytes(audio))
        logger.info(
            "edge-tts synthesized %d bytes, %d word timings -> %s",
            len(audio),
            len(word_timings),
            request.output_path.name,
        )
        return SynthesisResult(
            path=request.output_path,
            duration_seconds=0.0,  # the caller measures this with ffprobe
            voice=request.voice,
            provider=self.name,
            word_timings=word_timings,
        )


def _rate_string(rate: float) -> str:
    """Convert a 1.0-relative rate to Edge's ``+N%`` / ``-N%`` form."""
    percent = round((rate - 1.0) * 100)
    return f"{percent:+d}%"


def _pitch_string(pitch: float) -> str:
    return f"{round(pitch):+d}Hz"


def _is_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("401", "403", "unauthorized", "forbidden"))
