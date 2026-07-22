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
                "Hazır. ElevenLabs kotanızı kullanır."
                if configured
                else "Kullanmak için Ayarlar'dan bir ElevenLabs anahtarı ekleyin."
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
                "Konuşmacı listesi için ElevenLabs'e ulaşılamadı.",
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
                "Seslendirilecek metin yok.",
                suggestion="Bu sahneye metin yazın ya da sahneyi kapatın.",
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
                "ElevenLabs seslendirme sırasında yanıt vermedi.",
                details=str(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.TTS_PROVIDER_UNAVAILABLE,
                "ElevenLabs'e ulaşılamadı.",
                details=f"{type(exc).__name__}: {exc}",
            ) from exc

        self._raise_for_status(response)
        if not response.content:
            raise AppError(
                ErrorCode.TTS_FAILED,
                "ElevenLabs boş bir ses yanıtı döndürdü.",
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
                "ElevenLabs anahtarı tanımlı değil.",
                suggestion="Ayarlar → Servis anahtarları bölümünden ekleyin ya da ücretsiz olan Edge'i kullanın.",
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
                "ElevenLabs anahtarı kabul etmedi.",
                details=f"HTTP {response.status_code}: {body}",
                suggestion="Ayarlar → Servis anahtarları bölümünden anahtarı yeniden girin.",
            )
        if response.status_code == 429:
            raise AppError(
                ErrorCode.TTS_QUOTA_EXCEEDED,
                "ElevenLabs kotası ya da hız sınırı doldu.",
                details=f"HTTP 429: {body}",
                suggestion=(
                    "Kotanızın yenilenmesini bekleyin ya da ücretsiz olan Edge'e geçin."
                ),
            )
        raise AppError(
            ErrorCode.TTS_FAILED,
            f"ElevenLabs {response.status_code} hata kodu döndürdü.",
            details=body,
        )
