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
    collect_word_timings,
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


class KokoroVoiceInfo(CamelModel):
    id: str
    label: str
    gender: str
    grade: str
    training: str
    note: str
    lang_code: str
    language: str
    locale: str
    #: False for the languages Kokoro cannot time at word level.
    word_timings: bool


class KokoroLanguageInfo(CamelModel):
    code: str
    label: str
    locale: str
    extra_install: str
    word_timings: bool
    voice_count: int


class KokoroEnvironment(CamelModel):
    installed: bool
    model_cached: bool
    espeak_available: bool
    device: str
    cache_dir: str
    pip_install: str
    espeak_install: str
    repo_id: str
    sample_rate: int
    default_voice: str
    torch_version: str


class KokoroInfoResponse(CamelModel):
    """Everything the Audio tab needs to explain and drive Kokoro."""

    status: ProviderStatus
    environment: KokoroEnvironment
    voices: list[KokoroVoiceInfo]
    languages: list[KokoroLanguageInfo]
    recommended: list[str]
    device_options: list[str]
    min_speed: float
    max_speed: float
    setup_steps: list[str]
    usage_notes: list[str]
    input_notes: list[str]


def _label_for(project: Project, unit_id: str) -> str:
    if unit_id == INTRO_ID:
        return "Intro"
    if unit_id == OUTRO_ID:
        return "Outro"
    scene = project.scene_by_id(unit_id)
    if scene is None:
        return unit_id
    return scene.title or f"{scene.order + 1}. sahne"


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


@providers_router.get("/kokoro/info", response_model=KokoroInfoResponse)
def kokoro_info() -> KokoroInfoResponse:
    """Kokoro's catalogue, options and setup state.

    Served whether or not Kokoro is installed: the point is to let the Audio tab
    explain what installing it would give you, and exactly how, before anyone
    downloads a 350 MB model.
    """
    from app.models.enums import KokoroDevice
    from app.tts import kokoro as kokoro_provider
    from app.tts.kokoro_catalog import MAX_SPEED, MIN_SPEED, RECOMMENDED, VOICES

    environment = kokoro_provider.environment_report()
    installed = bool(environment["installed"])
    cached = bool(environment["modelCached"])

    setup_steps = [
        "Sanal ortamı etkinleştirin: source backend/.venv/bin/activate",
        f"Modeli kurun: {environment['pipInstall']}",
        f"Telaffuz motorunu kurun (önerilir): {environment['espeakInstall']}",
        "Uygulamayı yeniden başlatın; Kokoro burada 'hazır' görünecek.",
        f"İlk seslendirmede model bir kez indirilir (~350 MB, {environment['repoId']}) "
        "ve yanına küçük bir dil paketi (en_core_web_sm, ~13 MB) kurulur.",
    ]

    usage_notes = [
        "Model indikten sonra internet gerekmez; her şey bu bilgisayarda çalışır.",
        "Konuşma hızı 0,50× ile 2,00× arasındadır. Ton (pitch) ayarı Kokoro'da yoktur.",
        "İngilizce seslerde kelime kelime altyazı zamanlaması üretilir; diğer dillerde "
        "altyazılar metne göre tahmin edilir.",
        "'auto' bu bilgisayarda CPU kullanır ve bu bilinçli bir tercihtir: ölçümde "
        "gerçek zamanın 8-10 katı hızda seslendiriyor, yani 20 saniyelik bir sahne "
        "yaklaşık 2 saniye sürüyor. MPS bazı torch sürümlerinde hata verir; verirse "
        "otomatik olarak CPU'ya düşülür.",
        "Ses, sahne sahne önbelleğe alınır. Metni, sesi ya da hızı değiştirmedikçe "
        "yeniden üretilmez.",
    ]

    input_notes = [
        "Düz metin yazın. SSML, HTML etiketi ya da Markdown desteklenmez; bunlar "
        "seslendirmeden önce temizlenir.",
        "Bilimsel adları İçerik sekmesindeki telaffuz sözlüğüne ekleyin — "
        "örneğin 'Ectopistes migratorius' → 'ek-toh-PISS-teez my-gruh-TOR-ee-us'.",
        "Noktalama duraklamaları belirler: nokta uzun, virgül kısa duraklatır.",
        "Sayıları ve kısaltmaları yazıyla yazın ('1914' yerine 'nineteen fourteen') — "
        "okunuşları böylece kesinleşir.",
        "Uzun metinler model tarafından cümle sınırlarından otomatik bölünür; "
        "sahne başına 2-6 cümle en doğal sonucu verir.",
    ]

    if not installed:
        usage_notes.insert(0, "Kokoro şu anda kurulu değil — aşağıdaki adımları izleyin.")
    elif not cached:
        usage_notes.insert(0, "Kurulu, ama model henüz indirilmedi. İlk seslendirme "
                              "internet ister ve biraz uzun sürer.")

    return KokoroInfoResponse(
        status=get_provider("kokoro").status(),
        environment=KokoroEnvironment.model_validate(environment),
        voices=[
            KokoroVoiceInfo.model_validate(kokoro_provider.describe_voice(voice.id))
            for voice in VOICES
        ],
        languages=[
            KokoroLanguageInfo.model_validate(row)
            for row in kokoro_provider.language_summary()
        ],
        recommended=list(RECOMMENDED),
        device_options=[device.value for device in KokoroDevice],
        min_speed=MIN_SPEED,
        max_speed=MAX_SPEED,
        setup_steps=setup_steps,
        usage_notes=usage_notes,
        input_notes=input_notes,
    )


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
            "Bu ses örneği artık mevcut değil.",
            suggestion="Örneği yeniden oluşturun.",
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
                f"Tanınmayan bölüm(ler): {', '.join(missing)}",
                details=f"available: {', '.join(unit_map)}",
                suggestion="Projeyi yeniden açın; bu bölümler silinmiş olabilir.",
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

    # Sections that were already cached still have their stored timings; the
    # loop above only sees the ones it just synthesized.
    for unit_id, timings in collect_word_timings(project, paths).items():
        word_timings.setdefault(unit_id, timings)

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
            f"'{unit_id}' bölümü bu projede yok.",
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
            "Henüz altyazı oluşturulamıyor.",
            details="subtitle timing requires measured narration audio",
            suggestion="Önce metinleri seslendirin ya da ses dosyası yükleyin.",
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
            f"'{unit_id}' bölümü için altyazı yok.",
            suggestion="Önce bu bölümü seslendirin.",
        )
    return PlainTextResponse(render_srt(cues), media_type="application/x-subrip")
