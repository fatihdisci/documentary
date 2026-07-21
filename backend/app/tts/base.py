"""TTS provider interface.

The application never depends on a specific provider. Anything that can turn
text into an audio file on disk satisfies this protocol, including the
``imported`` provider that simply uses audio the user supplied — which is what
keeps the app fully usable with no network at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.models.base import CamelModel


class Voice(CamelModel):
    id: str
    name: str
    locale: str = ""
    gender: str = ""
    description: str = ""


class ProviderStatus(CamelModel):
    name: str
    available: bool
    message: str
    requires_api_key: bool = False
    api_key_configured: bool = False
    supports_rate: bool = False
    supports_pitch: bool = False
    supports_word_timings: bool = False
    #: True when the provider works with no network connection.
    offline: bool = False


@dataclass(frozen=True)
class WordTiming:
    word: str
    start_seconds: float
    end_seconds: float


@dataclass
class SynthesisRequest:
    text: str
    voice: str
    output_path: Path
    rate: float = 1.0
    pitch: float = 0.0
    #: Applied to the text before synthesis, e.g. scientific-name pronunciations.
    pronunciation: dict[str, str] = field(default_factory=dict)


@dataclass
class SynthesisResult:
    path: Path
    #: Measured with ffprobe by the caller, never estimated by the provider.
    duration_seconds: float
    voice: str
    provider: str
    #: Present only when the provider reports them. Used verbatim for subtitles.
    word_timings: list[WordTiming] = field(default_factory=list)


@runtime_checkable
class TTSProvider(Protocol):
    """What every provider must offer."""

    name: str

    def status(self) -> ProviderStatus:
        """Report availability now. Must never raise."""
        ...

    async def list_voices(self) -> list[Voice]:
        """Available voices. May be empty for providers without a catalogue."""
        ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Write audio to ``request.output_path`` and describe the result.

        Raises an AppError subclass with an actionable message on failure.
        """
        ...
