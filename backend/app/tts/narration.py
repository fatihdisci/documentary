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
            suggestion="Add narration, or disable this section so it is skipped.",
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
            suggestion="Re-upload the audio file, or switch this scene back to generated narration.",
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
            word_timings=[],
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

    _prune_stale(paths, unit_id, keep=target.name)
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
            f"Audio file '{relative_path}' is not in this project.",
            details=str(target),
        )
    info = probe_audio(target, settings=settings or get_settings())
    unit.audio_file = relative_path
    unit.audio_source = AudioSource.IMPORTED
    unit.audio_duration_seconds = info.duration_seconds
    unit.audio_hash = None  # imported audio is not derived from a hashable input
    return info.duration_seconds


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


def _prune_stale(paths: ProjectPaths, unit_id: str, *, keep: str) -> None:
    """Delete superseded generated audio for this unit; keep the current one."""
    for stale in paths.generated_audio.glob(f"{unit_id}-*"):
        if stale.name != keep:
            stale.unlink(missing_ok=True)


def _label(unit_id: str) -> str:
    if unit_id == INTRO_ID:
        return "The intro"
    if unit_id == OUTRO_ID:
        return "The outro"
    return f"Scene {unit_id}"
