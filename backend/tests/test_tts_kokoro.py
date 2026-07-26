"""The Kokoro provider.

Almost everything here runs without the optional dependency installed, which is
deliberate: the catalogue, the option surface and the failure messages are what
a user sees *before* deciding to install a 350 MB model, so they have to be
correct on a machine that has never seen torch.

The one thing that genuinely needs guarding is the timestamp arithmetic. Kokoro
reports each chunk's word timings from that chunk's own zero, so a multi-chunk
paragraph only lines up if every chunk after the first is shifted by the audio
already emitted. ``TestWordTimingOffsets`` pins that with a fake pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import KokoroDevice, TTSProviderName
from app.tts import kokoro as kokoro_module
from app.tts.base import SynthesisRequest, TTSProvider
from app.tts.kokoro import KokoroProvider
from app.tts.kokoro_catalog import (
    DEFAULT_VOICE,
    LANGUAGES,
    RECOMMENDED,
    VOICES,
    is_known,
    lang_code_for,
)
from app.tts.registry import get_provider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def provider() -> KokoroProvider:
    return KokoroProvider()


class TestRegistration:
    def test_is_registered_and_satisfies_the_protocol(self) -> None:
        assert isinstance(get_provider(TTSProviderName.KOKORO), TTSProvider)

    def test_status_never_raises_and_reports_its_limits(self, provider: KokoroProvider) -> None:
        status = provider.status()
        assert status.name == "kokoro"
        assert status.message
        assert status.requires_api_key is False
        # Kokoro has no pitch control; advertising one would be a lie the Audio
        # tab would render as a working slider.
        assert status.supports_pitch is False
        assert status.supports_rate is True

    def test_reports_offline_only_once_the_model_is_cached(
        self, provider: KokoroProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first run needs the network; every run after it does not."""
        monkeypatch.setattr(kokoro_module, "_package_available", lambda: True)

        monkeypatch.setattr(kokoro_module, "_model_is_cached", lambda: False)
        assert provider.status().offline is False

        monkeypatch.setattr(kokoro_module, "_model_is_cached", lambda: True)
        assert provider.status().offline is True

    def test_is_unavailable_with_install_instructions_when_missing(
        self, provider: KokoroProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(kokoro_module, "_package_available", lambda: False)
        status = provider.status()
        assert status.available is False
        assert "pip install" in status.message


class TestCatalogue:
    async def test_lists_every_voice_without_network_or_model(
        self, provider: KokoroProvider
    ) -> None:
        voices = await provider.list_voices()
        assert len(voices) == len(VOICES)
        assert {v.id for v in voices} == {v.id for v in VOICES}
        assert all(v.locale and v.gender for v in voices)

    def test_voice_ids_are_unique(self) -> None:
        ids = [voice.id for voice in VOICES]
        assert len(ids) == len(set(ids))

    def test_every_voice_belongs_to_a_known_language(self) -> None:
        for voice in VOICES:
            assert voice.lang_code in LANGUAGES
            # Kokoro encodes the language in the first character of the id.
            assert voice.id.startswith(voice.lang_code)

    def test_recommended_voices_all_exist_and_are_english(self) -> None:
        for voice_id in RECOMMENDED:
            assert is_known(voice_id)
            assert lang_code_for(voice_id) in {"a", "b"}

    def test_default_voice_exists_and_supports_word_timings(self) -> None:
        assert is_known(DEFAULT_VOICE)
        assert LANGUAGES[lang_code_for(DEFAULT_VOICE)].word_timings is True

    @pytest.mark.parametrize(
        ("voice_id", "expected"),
        [("af_heart", "a"), ("bm_george", "b"), ("jf_alpha", "j"), ("zm_yunxi", "z")],
    )
    def test_language_is_taken_from_the_id(self, voice_id: str, expected: str) -> None:
        assert lang_code_for(voice_id) == expected

    def test_unknown_voice_falls_back_to_american_english(self) -> None:
        assert lang_code_for("en-US-AndrewNeural") == "a"
        assert lang_code_for("") == "a"


class TestRejectsBadInputEarly:
    """Neither case should reach the model — or need it to be installed."""

    async def test_rejects_a_voice_from_another_provider(
        self, provider: KokoroProvider, tmp_path: Path
    ) -> None:
        with pytest.raises(AppError) as exc_info:
            await provider.synthesize(
                SynthesisRequest(
                    text="The dodo lived only on Mauritius.",
                    voice="en-US-AndrewNeural",  # an Edge voice
                    output_path=tmp_path / "out.mp3",
                )
            )
        assert exc_info.value.code is ErrorCode.TTS_FAILED
        assert DEFAULT_VOICE in (exc_info.value.suggestion or "")

    async def test_rejects_empty_narration(
        self, provider: KokoroProvider, tmp_path: Path
    ) -> None:
        with pytest.raises(AppError) as exc_info:
            await provider.synthesize(
                SynthesisRequest(text="   ", voice=DEFAULT_VOICE, output_path=tmp_path / "o.mp3")
            )
        assert exc_info.value.code is ErrorCode.MISSING_NARRATION


class TestDeviceResolution:
    def test_explicit_choices_are_honoured(self, settings, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(kokoro_module, "_mps_unusable", False)
        for device in (KokoroDevice.CPU, KokoroDevice.MPS, KokoroDevice.CUDA):
            settings.mutable.kokoro_device = device
            # str(KokoroDevice.CPU) is "KokoroDevice.CPU" — the resolver must
            # read .value or every device would silently become "auto".
            assert kokoro_module._resolve_device(settings) == device.value

    def test_auto_never_picks_mps(self, settings) -> None:  # noqa: ANN001
        settings.mutable.kokoro_device = KokoroDevice.AUTO
        assert kokoro_module._resolve_device(settings) in {"cpu", "cuda"}

    def test_mps_downgrades_to_cpu_once_it_has_failed(self, settings, monkeypatch) -> None:  # noqa: ANN001
        settings.mutable.kokoro_device = KokoroDevice.MPS
        monkeypatch.setattr(kokoro_module, "_mps_unusable", True)
        assert kokoro_module._resolve_device(settings) == "cpu"


# --- the timestamp arithmetic ----------------------------------------------


@dataclass
class FakeToken:
    text: str
    start_ts: float | None
    end_ts: float | None


class FakeResult:
    """One chunk as KPipeline yields it: audio plus chunk-relative timings."""

    def __init__(self, samples: int, tokens: list[FakeToken]) -> None:
        import numpy as np

        self.audio = np.zeros(samples, dtype="float32")
        self.tokens = tokens


class TestWordTimingOffsets:
    """Chunk-relative timestamps must be rebased onto the joined audio."""

    @staticmethod
    def _install(monkeypatch: pytest.MonkeyPatch, chunks: list[FakeResult]) -> None:
        rate = kokoro_module.SAMPLE_RATE
        assert rate == 24_000
        monkeypatch.setattr(
            kokoro_module, "_get_pipeline", lambda _lang, _device: lambda *a, **k: iter(chunks)
        )

    def test_later_chunks_are_shifted_by_the_audio_before_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rate = kokoro_module.SAMPLE_RATE
        self._install(
            monkeypatch,
            [
                # 2s of audio; words at 0.5s and 1.5s within the chunk.
                FakeResult(2 * rate, [FakeToken("Imagine", 0.5, 1.0), FakeToken("this", 1.5, 1.9)]),
                # 3s of audio; a word at 0.25s within the chunk -> 2.25s overall.
                FakeResult(3 * rate, [FakeToken("Then", 0.25, 0.75)]),
            ],
        )
        segments, timings = kokoro_module._run_pipeline("t", "af_heart", 1.0, "a", "cpu")

        assert len(segments) == 2
        assert [t.word for t in timings] == ["Imagine", "this", "Then"]
        assert timings[0].start_seconds == pytest.approx(0.5)
        assert timings[1].start_seconds == pytest.approx(1.5)
        # The one that matters: 2s of chunk one + 0.25s into chunk two.
        assert timings[2].start_seconds == pytest.approx(2.25)
        assert timings[2].end_seconds == pytest.approx(2.75)

    def test_timings_stay_monotonic_across_many_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rate = kokoro_module.SAMPLE_RATE
        self._install(
            monkeypatch,
            [FakeResult(rate, [FakeToken(f"w{i}", 0.1, 0.9)]) for i in range(6)],
        )
        _, timings = kokoro_module._run_pipeline("t", "af_heart", 1.0, "a", "cpu")
        starts = [t.start_seconds for t in timings]
        assert starts == sorted(starts)
        assert starts[-1] == pytest.approx(5.1)

    def test_untimed_and_blank_tokens_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-English chunks carry tokens with no timestamps at all."""
        rate = kokoro_module.SAMPLE_RATE
        self._install(
            monkeypatch,
            [
                FakeResult(
                    rate,
                    [
                        FakeToken("kept", 0.1, 0.4),
                        FakeToken("untimed", None, None),
                        FakeToken("   ", 0.5, 0.6),
                    ],
                )
            ],
        )
        _, timings = kokoro_module._run_pipeline("t", "jf_alpha", 1.0, "j", "cpu")
        assert [t.word for t in timings] == ["kept"]

    def test_empty_chunks_do_not_advance_the_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rate = kokoro_module.SAMPLE_RATE
        self._install(
            monkeypatch,
            [
                FakeResult(0, [FakeToken("dropped", 0.0, 0.1)]),
                FakeResult(rate, [FakeToken("first", 0.2, 0.5)]),
            ],
        )
        segments, timings = kokoro_module._run_pipeline("t", "af_heart", 1.0, "a", "cpu")
        assert len(segments) == 1
        assert [t.word for t in timings] == ["first"]
        assert timings[0].start_seconds == pytest.approx(0.2)


class TestInfoEndpoint:
    def test_describes_kokoro_whether_or_not_it_is_installed(self, client) -> None:  # noqa: ANN001
        response = client.get("/api/tts/kokoro/info")
        assert response.status_code == 200

        body = response.json()
        assert len(body["voices"]) == len(VOICES)
        assert body["environment"]["pipInstall"].startswith("pip install")
        assert body["deviceOptions"] == ["auto", "cpu", "mps", "cuda"]
        assert body["minSpeed"] == 0.5 and body["maxSpeed"] == 2.0
        # The panel is useless without these, so they are part of the contract.
        assert body["setupSteps"] and body["usageNotes"] and body["inputNotes"]
        assert body["recommended"]

    def test_voice_rows_carry_the_grade_and_timing_support(self, client) -> None:  # noqa: ANN001
        body = client.get("/api/tts/kokoro/info").json()
        by_id = {voice["id"]: voice for voice in body["voices"]}

        assert by_id["af_heart"]["grade"] == "A"
        assert by_id["af_heart"]["wordTimings"] is True
        # Only English gets word-level timings out of Kokoro.
        assert by_id["jf_alpha"]["wordTimings"] is False

    def test_appears_in_the_provider_list(self, client) -> None:  # noqa: ANN001
        names = [p["name"] for p in client.get("/api/tts/providers").json()["providers"]]
        assert "kokoro" in names
