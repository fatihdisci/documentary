"""The 'imported' provider: narration the user supplied themselves.

This provider is what guarantees the app never depends on a network service.
It synthesizes nothing — it reports that a scene's audio must come from an
upload, and the render pipeline uses that file directly.
"""

from __future__ import annotations

from app.errors import AppError, ErrorCode
from app.tts.base import ProviderStatus, SynthesisRequest, SynthesisResult, Voice


class ImportedAudioProvider:
    name = "imported"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            available=True,
            message="Use your own audio files. Works with no internet connection.",
            supports_rate=False,
            supports_pitch=False,
            supports_word_timings=False,
            offline=True,
        )

    async def list_voices(self) -> list[Voice]:
        return []

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Always an error — but a specific, actionable one.

        Reaching here means a scene is set to use imported audio and none has
        been uploaded. Saying so plainly is more useful than silently producing
        silence.
        """
        raise AppError(
            ErrorCode.MISSING_AUDIO,
            "This scene is set to use imported audio, but no audio file has been uploaded.",
            details=f"expected an upload for: {request.output_path.name}",
            suggestion=(
                "Upload a WAV, MP3 or M4A file for this scene, or switch the project's "
                "TTS provider to Edge to generate narration automatically."
            ),
        )
