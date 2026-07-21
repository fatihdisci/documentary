"""ElevenLabs provider — optional, requires an API key.

Entirely optional: the app's default workflow never needs a paid API. The key
is read from the secrets store and never logged or echoed back.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.tts.base import ProviderStatus, SynthesisRequest, SynthesisResult, Voice
from app.tts.pronunciation import apply_pronunciation, sanitize_for_tts

logger = logging.getLogger("evb.tts.elevenlabs")

API_ROOT = "https://api.elevenlabs.io/v1"
SECRET_KEY = "elevenlabs_api_key"
DEFAULT_MODEL = "eleven_multilingual_v2"
REQUEST_TIMEOUT = 180.0


class ElevenLabsProvider:
    name = "elevenlabs"

    def _api_key(self) -> str | None:
        return get_settings().get_secret(SECRET_KEY)

    def status(self) -> ProviderStatus:
        configured = self._api_key() is not None
        return ProviderStatus(
            name=self.name,
            available=configured,
            message=(
                "Ready. Uses your ElevenLabs quota."
                if configured
                else "Add an ElevenLabs API key in Settings to enable this provider."
            ),
            requires_api_key=True,
            api_key_configured=configured,
            supports_rate=False,
            supports_pitch=False,
            supports_word_timings=False,
        )

    async def list_voices(self) -> list[Voice]:
        key = self._require_key()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{API_ROOT}/voices", headers={"xi-api-key": key})
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.TTS_PROVIDER_UNAVAILABLE,
                "Could not reach ElevenLabs to list voices.",
                details=f"{type(exc).__name__}: {exc}",
            ) from exc

        self._raise_for_status(response)
        payload = response.json()
        return [
            Voice(
                id=str(entry.get("voice_id", "")),
                name=str(entry.get("name", "")),
                description=str(entry.get("description") or ""),
                gender=str((entry.get("labels") or {}).get("gender", "")),
                locale=str((entry.get("labels") or {}).get("accent", "")),
            )
            for entry in payload.get("voices", [])
            if entry.get("voice_id")
        ]

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        key = self._require_key()
        text = sanitize_for_tts(apply_pronunciation(request.text, request.pronunciation))
        if not text:
            raise AppError(
                ErrorCode.MISSING_NARRATION,
                "There is no narration text to synthesize.",
                suggestion="Add narration to this scene, or disable it.",
            )

        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{API_ROOT}/text-to-speech/{request.voice}",
                    headers={"xi-api-key": key, "accept": "audio/mpeg"},
                    json={
                        "text": text,
                        "model_id": DEFAULT_MODEL,
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                    },
                )
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.TTS_TIMEOUT,
                "ElevenLabs timed out while generating narration.",
                details=str(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.TTS_PROVIDER_UNAVAILABLE,
                "Could not reach ElevenLabs.",
                details=f"{type(exc).__name__}: {exc}",
            ) from exc

        self._raise_for_status(response)
        if not response.content:
            raise AppError(
                ErrorCode.TTS_FAILED,
                "ElevenLabs returned an empty audio response.",
                details=f"status={response.status_code}",
            )

        request.output_path.write_bytes(response.content)
        return SynthesisResult(
            path=request.output_path,
            duration_seconds=0.0,  # measured by the caller with ffprobe
            voice=request.voice,
            provider=self.name,
        )

    def _require_key(self) -> str:
        key = self._api_key()
        if not key:
            raise AppError(
                ErrorCode.TTS_INVALID_API_KEY,
                "No ElevenLabs API key is configured.",
                suggestion="Add one in Settings → API keys, or use Edge TTS, which is free.",
            )
        return key

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return

        # Never include the request headers in details: they carry the API key.
        body = response.text[:1000]
        if response.status_code in {401, 403}:
            raise AppError(
                ErrorCode.TTS_INVALID_API_KEY,
                "ElevenLabs rejected the API key.",
                details=f"HTTP {response.status_code}: {body}",
                suggestion="Re-enter your API key in Settings → API keys.",
            )
        if response.status_code == 429:
            raise AppError(
                ErrorCode.TTS_QUOTA_EXCEEDED,
                "ElevenLabs quota or rate limit reached.",
                details=f"HTTP 429: {body}",
                suggestion=(
                    "Wait for your quota to reset, or switch to Edge TTS, which is free."
                ),
            )
        raise AppError(
            ErrorCode.TTS_FAILED,
            f"ElevenLabs returned HTTP {response.status_code}.",
            details=body,
        )
