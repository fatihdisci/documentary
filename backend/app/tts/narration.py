"""Narration generation with content-hash caching.

The cache is the mechanism behind the invalidation matrix: a scene's audio is
keyed by exactly the inputs that change how it sounds. Edit a title and nothing
is regenerated; edit the narration, voice or rate and only that scene is.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode
from app.models.enums import AudioSource
from app.models.project import Project, Scene, Section
from app.storage.layout import ProjectPaths
from app.timing.probe import probe_audio
from app.tts.base import SynthesisRequest, WordTiming
from app.tts.registry import get_provider
from app.tts.timings import load_word_timings, save_word_timings

logger = logging.getLogger("evb.narration")

#: Sections are addressed by these ids alongside numeric scene ids.
INTRO_ID = "intro"
OUTRO_ID = "outro"


@dataclass
class NarrationOutcome:
    unit_id: str
    generated: bool
    reused: bool
    duration_seconds: float
    audio_file: str
    word_timings: list[WordTiming]


def audio_hash(*, text: str, provider: str, voice: str, rate: float, pitch: float,
               pronunciation: dict[str, str]) -> str:
    """Hash of exactly the inputs that affect the produced audio.

    Deliberately excludes titles, images, motion, transitions and export
    settings — changing any of those must not trigger re-synthesis.
    """
    payload = "\x1f".join(
        [
            text.strip(),
            provider,
            voice,
            f"{rate:.4f}",
            f"{pitch:.2f}",
            "\x1e".join(f"{k}={v}" for k, v in sorted(pronunciation.items())),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _unit_narration(unit: Scene | Section) -> str:
    return unit.narration.strip()


async def generate_for_unit(
    project: Project,
    unit: Scene | Section,
    unit_id: str,
    paths: ProjectPaths,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> NarrationOutcome:
    """Ensure ``unit`` has narration audio, generating it only if needed."""
    active = settings or get_settings()
    text = _unit_narration(unit)

    if not text:
        raise AppError(
            ErrorCode.MISSING_NARRATION,
            f"{_label(unit_id)} has no narration text.",
            suggestion="Metin yazın ya da bu bölümü kapatın.",
        )

    # User-supplied audio is never regenerated or overwritten.
    if unit.audio_source is AudioSource.IMPORTED and unit.audio_file:
        existing = _resolve(paths, unit.audio_file)
        if existing.is_file():
            info = probe_audio(existing, settings=active)
            unit.audio_duration_seconds = info.duration_seconds
            return NarrationOutcome(
                unit_id=unit_id,
                generated=False,
                reused=True,
                duration_seconds=info.duration_seconds,
                audio_file=unit.audio_file,
                word_timings=[],
            )
        raise AppError(
            ErrorCode.MISSING_AUDIO,
            f"{_label(unit_id)} points at imported audio that is missing: {unit.audio_file}",
            suggestion="Ses dosyasını tekrar yükleyin ya da bu sahneyi yeniden seslendirmeye alın.",
        )

    expected = audio_hash(
        text=text,
        provider=project.audio.tts_provider.value,
        voice=project.audio.voice,
        rate=project.audio.speech_rate,
        pitch=project.audio.speech_pitch,
        pronunciation=project.pronunciation,
    )

    relative = f"audio/generated/{unit_id}-{expected}.mp3"
    target = paths.root / relative

    if not force and unit.audio_hash == expected and target.is_file():
        info = probe_audio(target, settings=active)
        unit.audio_duration_seconds = info.duration_seconds
        unit.audio_file = relative
        logger.debug("reusing cached narration for %s", unit_id)
        return NarrationOutcome(
            unit_id=unit_id,
            generated=False,
            reused=True,
            duration_seconds=info.duration_seconds,
            audio_file=relative,
            # Cached audio keeps its word timings. Without this the subtitle
            # engine silently falls back to estimating from character counts
            # for every scene, which runs measurably ahead of the words.
            word_timings=load_word_timings(target),
        )

    provider = get_provider(project.audio.tts_provider)
    result = await provider.synthesize(
        SynthesisRequest(
            text=text,
            voice=project.audio.voice,
            output_path=target,
            rate=project.audio.speech_rate,
            pitch=project.audio.speech_pitch,
            pronunciation=project.pronunciation,
        )
    )

    # The provider's own duration estimate is ignored; measure the real file.
    info = probe_audio(result.path, settings=active)
    unit.audio_file = relative
    unit.audio_hash = expected
    unit.audio_source = AudioSource.GENERATED
    unit.audio_duration_seconds = info.duration_seconds

    timings_file = save_word_timings(target, result.word_timings)
    _prune_stale(paths, unit_id, keep={target.name, timings_file.name if timings_file else ""})
    logger.info("generated narration for %s: %.2fs", unit_id, info.duration_seconds)

    return NarrationOutcome(
        unit_id=unit_id,
        generated=True,
        reused=False,
        duration_seconds=info.duration_seconds,
        audio_file=relative,
        word_timings=result.word_timings,
    )


def attach_imported_audio(
    unit: Scene | Section,
    paths: ProjectPaths,
    relative_path: str,
    *,
    settings: Settings | None = None,
) -> float:
    """Point a scene at user-uploaded audio and measure its real duration."""
    target = _resolve(paths, relative_path)
    if not target.is_file():
        raise AppError(
            ErrorCode.MISSING_AUDIO,
            f"'{relative_path}' ses dosyası bu projede yok.",
            details=str(target),
        )
    info = probe_audio(target, settings=settings or get_settings())
    unit.audio_file = relative_path
    unit.audio_source = AudioSource.IMPORTED
    unit.audio_duration_seconds = info.duration_seconds
    unit.audio_hash = None  # imported audio is not derived from a hashable input
    return info.duration_seconds


def word_timings_for(unit: Scene | Section, paths: ProjectPaths) -> list[WordTiming]:
    """Word timings for a unit's current audio, if any were ever stored.

    Imported audio has none — nobody measured where its words fall — so those
    scenes legitimately fall back to the estimator.
    """
    if not unit.audio_file:
        return []
    try:
        audio = _resolve(paths, unit.audio_file)
    except AppError:
        return []
    return load_word_timings(audio) if audio.is_file() else []


def collect_word_timings(
    project: Project, paths: ProjectPaths
) -> dict[str, list[WordTiming]]:
    """Every unit's stored word timings, keyed by unit id.

    Callers used to gather timings only from units they had just synthesized,
    so a render over cached audio got none at all.
    """
    collected: dict[str, list[WordTiming]] = {}
    for unit_id, unit in iter_units(project):
        timings = word_timings_for(unit, paths)
        if timings:
            collected[unit_id] = timings
    return collected


def units_needing_audio(project: Project) -> list[tuple[str, Scene | Section]]:
    """Every enabled unit whose narration audio is missing or stale."""
    pending: list[tuple[str, Scene | Section]] = []

    for unit_id, unit in iter_units(project):
        if not _unit_narration(unit):
            continue
        if unit.audio_source is AudioSource.IMPORTED and unit.audio_file:
            continue
        expected = audio_hash(
            text=_unit_narration(unit),
            provider=project.audio.tts_provider.value,
            voice=project.audio.voice,
            rate=project.audio.speech_rate,
            pitch=project.audio.speech_pitch,
            pronunciation=project.pronunciation,
        )
        if unit.audio_hash != expected or not unit.audio_file:
            pending.append((unit_id, unit))
    return pending


def iter_units(project: Project) -> list[tuple[str, Scene | Section]]:
    """Intro, enabled scenes, outro — in timeline order."""
    units: list[tuple[str, Scene | Section]] = []
    if project.intro.enabled and _unit_narration(project.intro):
        units.append((INTRO_ID, project.intro))
    for scene in project.active_scenes:
        units.append((scene.id, scene))
    if project.outro.enabled and _unit_narration(project.outro):
        units.append((OUTRO_ID, project.outro))
    return units


def _resolve(paths: ProjectPaths, relative: str) -> Path:
    from app.storage.paths import safe_join

    return safe_join(paths.root, relative)


def _prune_stale(paths: ProjectPaths, unit_id: str, *, keep: set[str]) -> None:
    """Delete superseded generated audio for this unit; keep the current take.

    ``keep`` covers the audio *and* its word-timings side-car — pruning one
    without the other would leave a scene with audio but no timings, which is
    exactly the state that makes subtitles fall back to estimates.
    """
    for stale in paths.generated_audio.glob(f"{unit_id}-*"):
        if stale.name not in keep:
            stale.unlink(missing_ok=True)


def _label(unit_id: str) -> str:
    if unit_id == INTRO_ID:
        return "The intro"
    if unit_id == OUTRO_ID:
        return "The outro"
    return f"{unit_id} sahnesi"
