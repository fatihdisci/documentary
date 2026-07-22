"""Subtitle drift: the bug where cues ran ahead of the words.

Two independent causes, both regression-tested here:

1. Word timings lived only in the memory of the process that synthesized the
   audio. Narration is generated on the Audio tab and the render happens later,
   so by render time every clip was a cache hit and every cue fell back to being
   estimated from character counts — measured at up to 0.66s ahead of speech.
2. The estimator laid its first cue at t=0 of the audio, but a take opens with
   roughly 0.15s of silence, so every cue inherited that lead.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from app.models.project import Project, Scene, SubtitleStyle
from app.storage.repository import ProjectRepository
from app.timing.probe import measure_speech_onset
from app.timing.schedule import build_timeline
from app.timing.subtitles import build_cues
from app.tts.base import WordTiming
from app.tts.narration import collect_word_timings, generate_for_unit, word_timings_for
from app.tts.timings import (
    TIMINGS_SCHEMA_VERSION,
    load_word_timings,
    save_word_timings,
    timings_path_for,
)
from tests.conftest import requires_ffmpeg
from tests.factories import make_wav_bytes


def timings(*spans: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(word=w, start_seconds=s, end_seconds=e) for w, s, e in spans]


class TestTimingsStore:
    def test_round_trips_beside_the_audio(self, tmp_path) -> None:  # noqa: ANN001
        audio = tmp_path / "scene-abc123.mp3"
        audio.write_bytes(b"audio")
        written = save_word_timings(audio, timings(("Hello", 0.1, 0.5), ("world", 0.5, 1.0)))

        assert written == timings_path_for(audio)
        assert written.name == "scene-abc123.timings.json"
        assert written.parent == audio.parent

        loaded = load_word_timings(audio)
        assert [w.word for w in loaded] == ["Hello", "world"]
        assert loaded[0].start_seconds == pytest.approx(0.1)
        assert loaded[1].end_seconds == pytest.approx(1.0)

    def test_writes_nothing_when_there_are_no_timings(self, tmp_path) -> None:  # noqa: ANN001
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        assert save_word_timings(audio, []) is None
        assert not timings_path_for(audio).exists()

    def test_missing_file_is_not_an_error(self, tmp_path) -> None:  # noqa: ANN001
        assert load_word_timings(tmp_path / "nothing.mp3") == []

    def test_corrupt_timings_degrade_to_estimation(self, tmp_path) -> None:  # noqa: ANN001
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        timings_path_for(audio).write_text("{not json", "utf-8")
        assert load_word_timings(audio) == []

    def test_timings_from_another_schema_are_ignored(self, tmp_path) -> None:  # noqa: ANN001
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        save_word_timings(audio, timings(("Hi", 0.0, 0.5)))
        path = timings_path_for(audio)
        raw = json.loads(path.read_text("utf-8"))
        raw["schemaVersion"] = TIMINGS_SCHEMA_VERSION + 1
        path.write_text(json.dumps(raw), "utf-8")
        assert load_word_timings(audio) == []

    def test_timings_describing_a_different_take_are_ignored(self, tmp_path) -> None:  # noqa: ANN001
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        save_word_timings(audio, timings(("Hi", 0.0, 0.5)))
        path = timings_path_for(audio)
        raw = json.loads(path.read_text("utf-8"))
        raw["audioFile"] = "somebody-elses.mp3"
        path.write_text(json.dumps(raw), "utf-8")
        assert load_word_timings(audio) == []


class TestCachedNarrationKeepsItsTimings:
    """The core regression: a render over cached audio must still have timings."""

    @pytest.fixture
    def project(self, settings):  # noqa: ANN001, ANN201
        repository = ProjectRepository(settings)
        created = repository.create("Drift")
        paths = repository.paths_for(created.slug)
        paths.ensure()
        created.intro.enabled = False
        created.outro.enabled = False
        created.scenes = [Scene(title="One", narration="Hello there, world.")]
        return created, paths, repository

    async def test_a_cache_hit_returns_the_stored_timings(
        self, project, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        created, paths, repository = project
        scene = created.scenes[0]
        calls = {"n": 0}

        async def fake_synthesize(request):  # noqa: ANN001, ANN202
            from app.tts.base import SynthesisResult

            calls["n"] += 1
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(make_wav_bytes(1.5))
            return SynthesisResult(
                path=request.output_path, duration_seconds=0.0,
                voice=request.voice, provider="edge",
                word_timings=timings(("Hello", 0.2, 0.6), ("there", 0.6, 1.0),
                                     ("world", 1.0, 1.4)),
            )

        from app.tts import registry

        class FakeProvider:
            name = "edge"

            async def synthesize(self, request):  # noqa: ANN001, ANN202
                return await fake_synthesize(request)

        monkeypatch.setattr(registry, "get_provider", lambda _name: FakeProvider())
        monkeypatch.setattr(
            "app.tts.narration.get_provider", lambda _name: FakeProvider()
        )

        first = await generate_for_unit(created, scene, scene.id, paths, settings=settings)
        assert first.generated is True
        assert len(first.word_timings) == 3

        # A later render reloads the project and reuses the cached audio.
        repository.save(created)
        reloaded = repository.load(created.slug)
        second = await generate_for_unit(
            reloaded, reloaded.scenes[0], reloaded.scenes[0].id, paths, settings=settings
        )

        assert second.reused is True
        assert calls["n"] == 1, "the audio should not have been synthesized again"
        assert [w.word for w in second.word_timings] == ["Hello", "there", "world"], (
            "cached narration lost its word timings, so cues would be estimated"
        )

    async def test_collect_gathers_timings_the_render_never_generated(
        self, project, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        created, paths, repository = project
        scene = created.scenes[0]

        class FakeProvider:
            name = "edge"

            async def synthesize(self, request):  # noqa: ANN001, ANN202
                from app.tts.base import SynthesisResult

                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(make_wav_bytes(1.5))
                return SynthesisResult(
                    path=request.output_path, duration_seconds=0.0,
                    voice=request.voice, provider="edge",
                    word_timings=timings(("Hello", 0.2, 0.6), ("world", 0.6, 1.2)),
                )

        monkeypatch.setattr("app.tts.narration.get_provider", lambda _name: FakeProvider())
        await generate_for_unit(created, scene, scene.id, paths, settings=settings)
        repository.save(created)

        reloaded = repository.load(created.slug)
        collected = collect_word_timings(reloaded, paths)
        assert list(collected) == [reloaded.scenes[0].id]
        assert len(collected[reloaded.scenes[0].id]) == 2

    def test_imported_audio_has_no_timings_and_that_is_fine(
        self, project, settings
    ) -> None:  # noqa: ANN001
        from app.tts.narration import attach_imported_audio

        created, paths, _ = project
        paths.imported_audio.mkdir(parents=True, exist_ok=True)
        (paths.imported_audio / "voice.wav").write_bytes(make_wav_bytes(2.0))
        attach_imported_audio(
            created.scenes[0], paths, "audio/imported/voice.wav", settings=settings
        )
        assert word_timings_for(created.scenes[0], paths) == []
        assert collect_word_timings(created, paths) == {}

    async def test_regenerating_prunes_the_old_take_but_keeps_the_new_timings(
        self, project, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        created, paths, _ = project
        scene = created.scenes[0]

        class FakeProvider:
            name = "edge"

            async def synthesize(self, request):  # noqa: ANN001, ANN202
                from app.tts.base import SynthesisResult

                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(make_wav_bytes(1.5))
                return SynthesisResult(
                    path=request.output_path, duration_seconds=0.0,
                    voice=request.voice, provider="edge",
                    word_timings=timings(("Hello", 0.2, 0.6)),
                )

        monkeypatch.setattr("app.tts.narration.get_provider", lambda _name: FakeProvider())
        await generate_for_unit(created, scene, scene.id, paths, settings=settings)

        scene.narration = "Something completely different now."
        await generate_for_unit(created, scene, scene.id, paths, settings=settings)

        files = sorted(p.name for p in paths.generated_audio.glob(f"{scene.id}-*"))
        assert len(files) == 2, f"expected the take and its timings, found {files}"
        assert word_timings_for(scene, paths), "the surviving take lost its timings"


class TestEstimatorBaseline:
    style = SubtitleStyle()

    def test_cues_start_after_the_leading_silence(self) -> None:
        text = "One sentence here. And a second sentence. Then a third one to finish."
        without = build_cues(text, total_duration=10.0, style=self.style)
        with_lead = build_cues(text, total_duration=10.0, style=self.style, speech_start=0.4)

        assert without[0].start_seconds == pytest.approx(0.0)
        assert with_lead[0].start_seconds == pytest.approx(0.4)
        # Every later cue moves with it, and the last still ends with the audio.
        assert all(b.start_seconds > a.start_seconds for a, b in zip(without, with_lead, strict=True))
        assert with_lead[-1].end_seconds == pytest.approx(without[-1].end_seconds, abs=0.01)

    def test_an_implausible_onset_is_ignored(self) -> None:
        text = "One sentence here. And a second sentence."
        cues = build_cues(text, total_duration=4.0, style=self.style, speech_start=3.5)
        assert cues[0].start_seconds == pytest.approx(0.0)

    def test_word_timings_still_win_over_the_onset(self) -> None:
        text = "Hello world."
        cues = build_cues(
            text, total_duration=10.0, style=self.style, speech_start=2.0,
            word_timings=timings(("Hello", 0.5, 0.9), ("world", 0.9, 1.4)),
        )
        assert cues[0].start_seconds == pytest.approx(0.5)


class TestPronunciationAlignment:
    style = SubtitleStyle()

    def test_a_respelled_phrase_does_not_pull_later_cues_early(self) -> None:
        """Timings describe the *spoken* form, which a respelling makes longer.

        The matcher consumes word boundaries until their combined text covers
        the cue. Measured against the shorter displayed form it stops early,
        leaves the cursor mid-sentence, and starts the next cue on a word that
        belongs to the previous one. How far it drifts depends on how much
        longer the respelling is; a syllable-by-syllable one like this is the
        realistic worst case.
        """
        text = "The dodo is gone. Sailors ate every last one of them."
        pronunciation = {"dodo": "DOH doh oh oh oh oh"}
        spoken = timings(
            ("The", 0.0, 0.2),
            ("DOH", 0.2, 0.5), ("doh", 0.5, 0.8), ("oh", 0.8, 1.0),
            ("oh", 1.0, 1.2), ("oh", 1.2, 1.4), ("oh", 1.4, 1.6),
            ("is", 1.6, 1.8), ("gone.", 1.8, 2.3),
            ("Sailors", 2.8, 3.3), ("ate", 3.3, 3.6), ("every", 3.6, 4.0),
            ("last", 4.0, 4.3), ("one", 4.3, 4.6), ("of", 4.6, 4.7),
            ("them.", 4.7, 5.2),
        )

        aware = build_cues(
            text, total_duration=5.2, style=self.style,
            word_timings=spoken, pronunciation=pronunciation,
        )
        naive = build_cues(text, total_duration=5.2, style=self.style, word_timings=spoken)

        assert len(aware) == 2
        # The second cue belongs at 2.8s, where "Sailors" is actually said.
        assert aware[1].start_seconds == pytest.approx(2.8, abs=0.05)
        assert naive[1].start_seconds < aware[1].start_seconds - 0.5, (
            "without the substitution the matcher consumes too few words"
        )

    def test_alignment_is_unchanged_without_a_pronunciation_dictionary(self) -> None:
        text = "Hello there. Goodbye now."
        spoken = timings(
            ("Hello", 0.0, 0.4), ("there.", 0.4, 0.9),
            ("Goodbye", 1.2, 1.7), ("now.", 1.7, 2.1),
        )
        with_empty = build_cues(text, total_duration=2.1, style=self.style,
                                word_timings=spoken, pronunciation={})
        without = build_cues(text, total_duration=2.1, style=self.style, word_timings=spoken)
        assert [c.start_seconds for c in with_empty] == [c.start_seconds for c in without]


@requires_ffmpeg
class TestSpeechOnsetMeasurement:
    def _clip(self, path, settings, *, lead: float, tone: float) -> None:  # noqa: ANN001
        """Silence for ``lead`` seconds, then an audible tone."""
        subprocess.run(  # noqa: S603
            [
                settings.require_tool("ffmpeg"), "-hide_banner", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={lead:g}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={tone:g}",
                "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]",
                "-map", "[a]", "-c:a", "pcm_s16le", str(path),
            ],
            check=True, capture_output=True,
        )

    def test_measures_a_known_lead(self, tmp_path, settings) -> None:  # noqa: ANN001
        path = tmp_path / "lead.wav"
        self._clip(path, settings, lead=0.6, tone=2.0)
        assert measure_speech_onset(path, settings=settings) == pytest.approx(0.6, abs=0.08)

    def test_reports_zero_when_speech_starts_immediately(
        self, tmp_path, settings
    ) -> None:  # noqa: ANN001
        path = tmp_path / "immediate.wav"
        path.write_bytes(make_wav_bytes(2.0))
        assert measure_speech_onset(path, settings=settings) == pytest.approx(0.0, abs=0.06)

    def test_an_implausibly_long_lead_is_not_trusted(
        self, tmp_path, settings
    ) -> None:  # noqa: ANN001
        path = tmp_path / "mostly-silence.wav"
        self._clip(path, settings, lead=3.0, tone=0.5)
        # Trimming most of the clip would be a guess, not a measurement.
        assert measure_speech_onset(path, settings=settings) == 0.0


class TestTimelineWiring:
    def test_speech_starts_reach_the_cues(self) -> None:
        project = Project(name="Wiring", slug="wiring")
        project.intro.enabled = False
        project.outro.enabled = False
        project.scenes = [
            Scene(
                id="s1",
                narration="One sentence here. And a second sentence to follow it.",
                audio_duration_seconds=8.0,
            )
        ]
        lead_in = project.video.scene_lead_in_seconds

        plain = build_timeline(project, validate=False)
        shifted = build_timeline(project, speech_starts={"s1": 0.5}, validate=False)

        assert plain.cues[0].start_seconds == pytest.approx(lead_in, abs=0.01)
        assert shifted.cues[0].start_seconds == pytest.approx(lead_in + 0.5, abs=0.01)

    def test_word_timings_reach_the_cues(self) -> None:
        project = Project(name="Wiring", slug="wiring")
        project.intro.enabled = False
        project.outro.enabled = False
        project.scenes = [
            Scene(id="s1", narration="Hello world.", audio_duration_seconds=4.0)
        ]
        timeline = build_timeline(
            project,
            word_timings={"s1": timings(("Hello", 1.0, 1.4), ("world.", 1.4, 2.0))},
            validate=False,
        )
        entry = timeline.entry("s1")
        assert timeline.cues[0].start_seconds == pytest.approx(
            entry.narration_start_seconds + 1.0, abs=0.01
        )
