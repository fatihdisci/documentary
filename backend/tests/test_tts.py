"""TTS provider abstraction, caching, pronunciation, and the offline path.

The offline tests matter most: the app must render a complete video with no
online TTS provider reachable at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import AppError, ErrorCode
from app.models.enums import AudioSource, TTSProviderName
from app.models.project import Project, Scene
from app.storage.repository import ProjectRepository
from app.tts.base import ProviderStatus, SynthesisRequest, SynthesisResult, TTSProvider, Voice
from app.tts.narration import (
    attach_imported_audio,
    audio_hash,
    generate_for_unit,
    units_needing_audio,
)
from app.tts.pronunciation import apply_pronunciation, sanitize_for_tts
from app.tts.registry import get_provider, provider_names, provider_status_summary
from tests.conftest import requires_ffmpeg
from tests.factories import make_wav_bytes


class FakeProvider:
    """A real provider implementation that writes real audio, without network."""

    name = "fake"

    def __init__(self, *, seconds_per_word: float = 0.3) -> None:
        self.seconds_per_word = seconds_per_word
        self.calls: list[SynthesisRequest] = []

    def status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, available=True, message="test provider", offline=True)

    async def list_voices(self) -> list[Voice]:
        return [Voice(id="test-voice", name="Test Voice")]

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.calls.append(request)
        duration = max(0.5, len(request.text.split()) * self.seconds_per_word)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(make_wav_bytes(duration))
        return SynthesisResult(
            path=request.output_path, duration_seconds=0.0, voice=request.voice, provider=self.name
        )


@pytest.fixture
def repository(settings) -> ProjectRepository:  # noqa: ANN001
    return ProjectRepository(settings)


@pytest.fixture
def project_with_scene(repository: ProjectRepository):  # noqa: ANN201
    project = repository.create("Dodo")
    project.scenes = [Scene(title="Habitat", narration="The dodo lived only on Mauritius.")]
    repository.save(project)
    return project, repository.paths_for(project.slug)


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr("app.tts.narration.get_provider", lambda _name: provider)
    return provider


class TestRegistry:
    def test_all_providers_are_registered(self) -> None:
        assert set(provider_names()) == {"edge", "imported", "elevenlabs"}

    def test_unknown_provider_is_rejected_clearly(self) -> None:
        with pytest.raises(AppError) as exc_info:
            get_provider("nonexistent")
        assert exc_info.value.code is ErrorCode.TTS_PROVIDER_UNAVAILABLE
        assert "edge" in (exc_info.value.details or "")

    def test_every_provider_satisfies_the_protocol(self) -> None:
        for name in provider_names():
            assert isinstance(get_provider(name), TTSProvider)

    def test_status_summary_never_raises(self) -> None:
        summary = provider_status_summary()
        assert set(summary) == {"edge", "imported", "elevenlabs"}
        for status in summary.values():
            assert status.message

    def test_imported_provider_is_always_available_and_offline(self) -> None:
        """This is what guarantees the app works with no network."""
        status = get_provider(TTSProviderName.IMPORTED).status()
        assert status.available is True
        assert status.offline is True

    def test_elevenlabs_is_unavailable_without_a_key(self) -> None:
        status = get_provider(TTSProviderName.ELEVENLABS).status()
        assert status.available is False
        assert status.requires_api_key is True
        assert "Ayarlar" in status.message


class TestPronunciation:
    def test_replaces_a_scientific_name(self) -> None:
        result = apply_pronunciation(
            "The dodo, Raphus cucullatus, was flightless.",
            {"Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"},
        )
        assert "RAH-fus koo-koo-LAH-tus" in result
        assert "Raphus cucullatus" not in result

    def test_is_case_insensitive(self) -> None:
        assert "MOW-rish-us" in apply_pronunciation("mauritius", {"Mauritius": "MOW-rish-us"})

    def test_longer_keys_win_over_shorter_ones(self) -> None:
        result = apply_pronunciation(
            "Raphus cucullatus lived there.",
            {"Raphus": "RAF-us", "Raphus cucullatus": "RAH-fus koo-koo-LAH-tus"},
        )
        assert result.startswith("RAH-fus koo-koo-LAH-tus")

    def test_does_not_match_inside_a_word(self) -> None:
        assert apply_pronunciation("preformed", {"form": "FORM"}) == "preformed"

    def test_empty_dictionary_is_a_noop(self) -> None:
        assert apply_pronunciation("unchanged text", {}) == "unchanged text"


class TestSanitize:
    def test_strips_characters_that_break_ssml(self) -> None:
        cleaned = sanitize_for_tts("A <break> & test")
        assert "<" not in cleaned and ">" not in cleaned and "&" not in cleaned
        assert "and" in cleaned

    def test_normalizes_typographic_punctuation(self) -> None:
        cleaned = sanitize_for_tts("The dodo’s world — gone …")
        assert "’" not in cleaned
        assert "—" not in cleaned
        assert "..." in cleaned

    def test_preserves_apostrophes_as_plain_quotes(self) -> None:
        assert "dodo's" in sanitize_for_tts("the dodo’s beak")

    def test_collapses_whitespace(self) -> None:
        assert sanitize_for_tts("  a\n\n  b  ") == "a b"

    def test_handles_unicode_without_crashing(self) -> None:
        assert sanitize_for_tts("Réunion Ibis — Thréskiornis solitarius")


class TestAudioHash:
    def _hash(self, **overrides: object) -> str:
        base = dict(
            text="Some narration.", provider="edge", voice="en-US-GuyNeural",
            rate=1.0, pitch=0.0, pronunciation={},
        )
        base.update(overrides)
        return audio_hash(**base)  # type: ignore[arg-type]

    def test_is_stable_for_identical_inputs(self) -> None:
        assert self._hash() == self._hash()

    @pytest.mark.parametrize(
        "change",
        [
            {"text": "Different narration."},
            {"voice": "en-GB-RyanNeural"},
            {"rate": 1.1},
            {"pitch": 5.0},
            {"provider": "elevenlabs"},
            {"pronunciation": {"Dodo": "DOH-doh"}},
        ],
    )
    def test_changes_when_an_audio_affecting_input_changes(self, change: dict) -> None:
        assert self._hash(**change) != self._hash()

    def test_ignores_surrounding_whitespace(self) -> None:
        assert self._hash(text="  Some narration.  ") == self._hash()


@requires_ffmpeg
class TestNarrationGeneration:
    async def test_generates_and_measures_real_duration(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]

        outcome = await generate_for_unit(project, scene, scene.id, paths)

        assert outcome.generated is True
        assert outcome.duration_seconds > 0
        # The duration is measured from the file, not taken from the provider,
        # which reported 0.0.
        assert scene.audio_duration_seconds == pytest.approx(outcome.duration_seconds)
        assert (paths.root / outcome.audio_file).is_file()

    async def test_second_call_reuses_the_cached_audio(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]

        await generate_for_unit(project, scene, scene.id, paths)
        outcome = await generate_for_unit(project, scene, scene.id, paths)

        assert outcome.reused is True
        assert outcome.generated is False
        assert len(fake_provider.calls) == 1, "must not re-synthesize unchanged narration"

    async def test_editing_narration_regenerates(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]

        await generate_for_unit(project, scene, scene.id, paths)
        scene.narration = "Completely different narration text now."
        outcome = await generate_for_unit(project, scene, scene.id, paths)

        assert outcome.generated is True
        assert len(fake_provider.calls) == 2

    async def test_changing_the_title_does_not_regenerate(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        """The cache invalidation matrix: a title edit must not cost a TTS call."""
        project, paths = project_with_scene
        scene = project.scenes[0]

        await generate_for_unit(project, scene, scene.id, paths)
        scene.title = "A Completely New Title"
        scene.animation_preset = scene.animation_preset  # touch other fields too
        scene.focus_x = 0.2
        outcome = await generate_for_unit(project, scene, scene.id, paths)

        assert outcome.reused is True
        assert len(fake_provider.calls) == 1

    async def test_force_regenerates_even_when_cached(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]
        await generate_for_unit(project, scene, scene.id, paths)
        await generate_for_unit(project, scene, scene.id, paths, force=True)
        assert len(fake_provider.calls) == 2

    async def test_stale_audio_for_the_same_scene_is_pruned(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]

        await generate_for_unit(project, scene, scene.id, paths)
        scene.narration = "New narration entirely."
        await generate_for_unit(project, scene, scene.id, paths)

        remaining = list(paths.generated_audio.glob(f"{scene.id}-*"))
        assert len(remaining) == 1, "the superseded file should be cleaned up"

    async def test_pronunciation_reaches_the_provider(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        project.pronunciation = {"Mauritius": "muh-RISH-us"}
        scene = project.scenes[0]

        await generate_for_unit(project, scene, scene.id, paths)

        assert fake_provider.calls[0].pronunciation == {"Mauritius": "muh-RISH-us"}

    async def test_empty_narration_is_rejected_with_guidance(
        self, project_with_scene, fake_provider: FakeProvider
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]
        scene.narration = "   "

        with pytest.raises(AppError) as exc_info:
            await generate_for_unit(project, scene, scene.id, paths)
        assert exc_info.value.code is ErrorCode.MISSING_NARRATION
        assert exc_info.value.suggestion


@requires_ffmpeg
class TestOfflineWorkflow:
    """The app must be fully usable with no online TTS provider."""

    async def test_imported_audio_is_measured_and_never_regenerated(
        self, project_with_scene, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, paths = project_with_scene
        project.audio.tts_provider = TTSProviderName.IMPORTED
        scene = project.scenes[0]

        # The user uploads their own narration.
        paths.imported_audio.mkdir(parents=True, exist_ok=True)
        (paths.imported_audio / "take1.wav").write_bytes(make_wav_bytes(4.25))
        duration = attach_imported_audio(scene, paths, "audio/imported/take1.wav")

        assert duration == pytest.approx(4.25, abs=0.05)
        assert scene.audio_source is AudioSource.IMPORTED

        # Any attempt to synthesize must reuse the file, not call a provider.
        def explode(_name: object) -> object:
            raise AssertionError("no TTS provider may be contacted for imported audio")

        monkeypatch.setattr("app.tts.narration.get_provider", explode)
        outcome = await generate_for_unit(project, scene, scene.id, paths)

        assert outcome.reused is True
        assert outcome.duration_seconds == pytest.approx(4.25, abs=0.05)

    async def test_imported_provider_without_audio_says_exactly_what_to_do(
        self, project_with_scene
    ) -> None:
        project, paths = project_with_scene
        project.audio.tts_provider = TTSProviderName.IMPORTED
        scene = project.scenes[0]

        with pytest.raises(AppError) as exc_info:
            await generate_for_unit(project, scene, scene.id, paths)

        error = exc_info.value
        assert error.code is ErrorCode.MISSING_AUDIO
        assert "yükleyin" in error.suggestion.lower()

    async def test_missing_imported_file_is_reported(self, project_with_scene) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]
        scene.audio_source = AudioSource.IMPORTED
        scene.audio_file = "audio/imported/vanished.wav"

        with pytest.raises(AppError) as exc_info:
            await generate_for_unit(project, scene, scene.id, paths)
        assert exc_info.value.code is ErrorCode.MISSING_AUDIO
        assert "vanished.wav" in exc_info.value.message

    def test_imported_audio_is_never_listed_as_needing_generation(
        self, project_with_scene
    ) -> None:
        project, paths = project_with_scene
        scene = project.scenes[0]
        paths.imported_audio.mkdir(parents=True, exist_ok=True)
        (paths.imported_audio / "take1.wav").write_bytes(make_wav_bytes(2.0))
        attach_imported_audio(scene, paths, "audio/imported/take1.wav")

        assert units_needing_audio(project) == []

    def test_path_traversal_in_an_audio_reference_is_blocked(self, project_with_scene) -> None:
        project, paths = project_with_scene
        with pytest.raises(AppError) as exc_info:
            attach_imported_audio(project.scenes[0], paths, "../../../../etc/passwd")
        assert exc_info.value.code is ErrorCode.PATH_TRAVERSAL


class TestPendingWork:
    def test_lists_only_units_that_need_audio(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        project.scenes = [
            Scene(narration="First scene narration."),
            Scene(narration=""),  # nothing to say
            Scene(narration="Third scene narration."),
        ]
        project.intro.enabled = False
        project.outro.enabled = False

        pending = units_needing_audio(project)
        assert len(pending) == 2

    def test_a_voice_change_marks_everything_stale(self, repository: ProjectRepository) -> None:
        project = repository.create("Dodo")
        scene = Scene(narration="Some narration.")
        # Seed a hash that matches the project's current voice settings, whatever
        # the defaults are, so only the change below makes it stale.
        scene.audio_hash = audio_hash(
            text="Some narration.",
            provider=project.audio.tts_provider.value,
            voice=project.audio.voice,
            rate=project.audio.speech_rate,
            pitch=project.audio.speech_pitch,
            pronunciation={},
        )
        scene.audio_file = "audio/generated/x.mp3"
        project.scenes = [scene]
        project.intro.enabled = False
        project.outro.enabled = False

        assert units_needing_audio(project) == []

        project.audio.voice = "en-GB-SoniaNeural"
        assert len(units_needing_audio(project)) == 1
