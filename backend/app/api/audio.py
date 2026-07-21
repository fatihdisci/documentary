"""Narration, voices, timing and subtitle endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import Field

from app.api.projects import _read_upload, repo
from app.config import get_settings
from app.errors import ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.project import Project
from app.storage import media
from app.timing.schedule import build_timeline, duration_summary
from app.timing.subtitles import render_srt
from app.tts.base import ProviderStatus, Voice, WordTiming
from app.tts.narration import (
    INTRO_ID,
    OUTRO_ID,
    attach_imported_audio,
    generate_for_unit,
    iter_units,
    units_needing_audio,
)
from app.tts.registry import get_provider, provider_status_summary

logger = logging.getLogger("evb.api.audio")

router = APIRouter(prefix="/api/projects/{slug}/audio", tags=["audio"])
providers_router = APIRouter(prefix="/api/tts", tags=["tts"])


class GenerateRequest(CamelModel):
    #: Empty means "everything that needs it".
    unit_ids: list[str] = Field(default_factory=list)
    force: bool = False


class UnitResult(CamelModel):
    unit_id: str
    label: str
    generated: bool
    reused: bool
    duration_seconds: float
    audio_file: str
    audio_url: str


class GenerateResponse(CamelModel):
    project: Project
    results: list[UnitResult]
    generated_count: int
    reused_count: int
    timing: dict


class TimingResponse(CamelModel):
    summary: dict
    entries: list[dict]
    warnings: list[str]
    cue_count: int


class ProviderListResponse(CamelModel):
    providers: list[ProviderStatus]


def _label_for(project: Project, unit_id: str) -> str:
    if unit_id == INTRO_ID:
        return "Intro"
    if unit_id == OUTRO_ID:
        return "Outro"
    scene = project.scene_by_id(unit_id)
    if scene is None:
        return unit_id
    return scene.title or f"Scene {scene.order + 1}"


def _audio_url(slug: str, relative: str) -> str:
    kind = "imported" if "/imported/" in relative else "generated"
    return f"/api/projects/{slug}/media/audio/{kind}/{relative.rsplit('/', 1)[-1]}"


# --- providers and voices ---------------------------------------------------


@providers_router.get("/providers", response_model=ProviderListResponse)
def list_providers() -> ProviderListResponse:
    return ProviderListResponse(providers=list(provider_status_summary().values()))


@providers_router.get("/voices", response_model=list[Voice])
async def list_voices(provider: str = Query(default="edge")) -> list[Voice]:
    return await get_provider(provider).list_voices()


@providers_router.post("/preview", response_class=PlainTextResponse)
async def preview_voice(
    provider: str = Query(default="edge"),
    voice: str = Query(...),
    text: str = Query(default="The dodo was a flightless bird found only on Mauritius."),
) -> PlainTextResponse:
    """Synthesize a short sample into the temp directory and return its path.

    Returns a URL the frontend can play.
    """
    from app.storage.paths import slugify
    from app.tts.base import SynthesisRequest

    settings = get_settings()
    target = settings.temp_dir / f"preview-{slugify(voice)}.mp3"
    await get_provider(provider).synthesize(
        SynthesisRequest(text=text[:400], voice=voice, output_path=target)
    )
    return PlainTextResponse(f"/api/tts/preview-file?name={target.name}")


@providers_router.get("/preview-file")
def preview_file(name: str) -> object:
    from fastapi.responses import FileResponse

    from app.storage.paths import safe_join

    target = safe_join(get_settings().temp_dir, name)
    if not target.is_file():
        raise NotFoundError(
            ErrorCode.MISSING_AUDIO,
            "That voice preview is no longer available.",
            suggestion="Generate the preview again.",
        )
    return FileResponse(target, media_type="audio/mpeg")


# --- narration generation ---------------------------------------------------


@router.post("/generate", response_model=GenerateResponse)
async def generate_narration(slug: str, request: GenerateRequest) -> GenerateResponse:
    repository = repo()
    project = repository.load(slug)
    paths = repository.paths_for(slug)

    unit_map = dict(iter_units(project))
    if request.unit_ids:
        missing = [uid for uid in request.unit_ids if uid not in unit_map]
        if missing:
            raise ValidationError(
                ErrorCode.SCHEMA_VALIDATION,
                f"Unknown section(s): {', '.join(missing)}",
                details=f"available: {', '.join(unit_map)}",
                suggestion="Reload the project; these sections may have been deleted.",
            )
        targets = [(uid, unit_map[uid]) for uid in request.unit_ids]
    elif request.force:
        targets = list(unit_map.items())
    else:
        targets = units_needing_audio(project)

    results: list[UnitResult] = []
    word_timings: dict[str, list[WordTiming]] = {}

    for unit_id, unit in targets:
        outcome = await generate_for_unit(project, unit, unit_id, paths, force=request.force)
        if outcome.word_timings:
            word_timings[unit_id] = outcome.word_timings
        results.append(
            UnitResult(
                unit_id=unit_id,
                label=_label_for(project, unit_id),
                generated=outcome.generated,
                reused=outcome.reused,
                duration_seconds=round(outcome.duration_seconds, 3),
                audio_file=outcome.audio_file,
                audio_url=_audio_url(slug, outcome.audio_file),
            )
        )

    repository.save(project)

    # Report the resulting timeline, but never fail generation because the
    # timeline is not yet valid — the user may still be filling scenes in.
    try:
        timeline = build_timeline(project, word_timings=word_timings)
        timing = duration_summary(timeline, project)
    except Exception as exc:  # noqa: BLE001
        timing = {"error": str(exc)}

    return GenerateResponse(
        project=project,
        results=results,
        generated_count=sum(1 for r in results if r.generated),
        reused_count=sum(1 for r in results if r.reused),
        timing=timing,
    )


@router.post("/import/{unit_id}", response_model=GenerateResponse)
async def import_audio(slug: str, unit_id: str, file: UploadFile = File(...)) -> GenerateResponse:
    """Attach user-supplied narration audio to one section."""
    repository = repo()
    project = repository.load(slug)
    paths = repository.paths_for(slug)

    unit_map = dict(iter_units(project))
    unit = unit_map.get(unit_id)
    if unit is None:
        # iter_units skips sections with no narration; fall back to a direct lookup
        # so audio can be attached before narration text is written.
        if unit_id == INTRO_ID:
            unit = project.intro
        elif unit_id == OUTRO_ID:
            unit = project.outro
        else:
            unit = project.scene_by_id(unit_id)
    if unit is None:
        raise NotFoundError(
            ErrorCode.PROJECT_NOT_FOUND,
            f"Section '{unit_id}' is not in this project.",
        )

    settings = get_settings()
    data = await _read_upload(file, max_mb=settings.mutable.max_upload_mb * 2)
    stored = media.store_imported_audio(paths, data, file.filename or "narration.wav")
    duration = attach_imported_audio(unit, paths, f"audio/imported/{stored.name}")
    repository.save(project)

    try:
        timing = duration_summary(build_timeline(project), project)
    except Exception as exc:  # noqa: BLE001
        timing = {"error": str(exc)}

    return GenerateResponse(
        project=project,
        results=[
            UnitResult(
                unit_id=unit_id,
                label=_label_for(project, unit_id),
                generated=False,
                reused=True,
                duration_seconds=round(duration, 3),
                audio_file=f"audio/imported/{stored.name}",
                audio_url=_audio_url(slug, f"audio/imported/{stored.name}"),
            )
        ],
        generated_count=0,
        reused_count=1,
        timing=timing,
    )


# --- timing and subtitles ---------------------------------------------------


@router.get("/timing", response_model=TimingResponse)
def get_timing(slug: str) -> TimingResponse:
    """The computed timeline. Shown before rendering so runtime is never a surprise."""
    project = repo().load(slug)
    timeline = build_timeline(project, validate=False)
    return TimingResponse(
        summary=duration_summary(timeline, project),
        entries=[
            {
                "unitId": e.unit_id,
                "kind": e.kind,
                "index": e.index,
                "label": _label_for(project, e.unit_id),
                "startSeconds": e.start_seconds,
                "durationSeconds": e.duration_seconds,
                "narrationStartSeconds": e.narration_start_seconds,
                "narrationEndSeconds": e.narration_end_seconds,
                "transition": e.transition.value,
                "transitionDurationSeconds": e.transition_duration,
            }
            for e in timeline.entries
        ],
        warnings=timeline.warnings,
        cue_count=len(timeline.cues),
    )


@router.get("/subtitles.srt", response_class=PlainTextResponse)
def get_subtitles(slug: str) -> PlainTextResponse:
    project = repo().load(slug)
    timeline = build_timeline(project, validate=False)
    if not timeline.cues:
        raise ValidationError(
            ErrorCode.MISSING_AUDIO,
            "No subtitles can be generated yet.",
            details="subtitle timing requires measured narration audio",
            suggestion="Generate or import narration audio first, then try again.",
        )
    return PlainTextResponse(render_srt(timeline.cues), media_type="application/x-subrip")


@router.get("/subtitles/{unit_id}.srt", response_class=PlainTextResponse)
def get_scene_subtitles(slug: str, unit_id: str) -> PlainTextResponse:
    project = repo().load(slug)
    timeline = build_timeline(project, validate=False)
    cues = timeline.cues_by_unit.get(unit_id)
    if not cues:
        raise NotFoundError(
            ErrorCode.MISSING_AUDIO,
            f"No subtitles exist for section '{unit_id}'.",
            suggestion="Generate narration audio for this section first.",
        )
    return PlainTextResponse(render_srt(cues), media_type="application/x-subrip")
