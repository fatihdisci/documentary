"""Provider lookup and status reporting."""

from __future__ import annotations

from app.errors import ErrorCode, ValidationError
from app.models.enums import TTSProviderName
from app.tts.base import ProviderStatus, TTSProvider
from app.tts.edge import EdgeTTSProvider
from app.tts.elevenlabs import ElevenLabsProvider
from app.tts.imported import ImportedAudioProvider

#: Instantiated once: providers are stateless apart from cached voice lists.
_PROVIDERS: dict[str, TTSProvider] = {
    TTSProviderName.EDGE.value: EdgeTTSProvider(),
    TTSProviderName.IMPORTED.value: ImportedAudioProvider(),
    TTSProviderName.ELEVENLABS.value: ElevenLabsProvider(),
}


def get_provider(name: str | TTSProviderName) -> TTSProvider:
    key = name.value if isinstance(name, TTSProviderName) else str(name)
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise ValidationError(
            ErrorCode.TTS_PROVIDER_UNAVAILABLE,
            f"Unknown TTS provider '{key}'.",
            details=f"available: {', '.join(sorted(_PROVIDERS))}",
            suggestion="Choose a provider from the Audio tab.",
        )
    return provider


def provider_names() -> list[str]:
    return sorted(_PROVIDERS)


def provider_status_summary() -> dict[str, ProviderStatus]:
    """Status of every provider. Never raises — used by Diagnostics."""
    summary: dict[str, ProviderStatus] = {}
    for name, provider in _PROVIDERS.items():
        try:
            summary[name] = provider.status()
        except Exception as exc:  # noqa: BLE001 - a broken provider must not hide the rest
            summary[name] = ProviderStatus(
                name=name,
                available=False,
                message=f"Status check failed: {type(exc).__name__}: {exc}",
            )
    return summary
