"""Kokoro provider — an 82M-parameter neural voice that runs on this machine.

Unlike Edge and ElevenLabs, nothing leaves the computer here: after the model
has been downloaded once it synthesizes with no network at all, which makes it
the only *generative* provider that keeps the offline promise the ``imported``
provider makes.

Two details drive the implementation:

* Kokoro reports **per-token timestamps** for English voices, so subtitles keep
  the word-level accuracy Edge gives us instead of falling back to the
  character-count estimator. The timestamps are per *chunk*, so they are offset
  by the audio emitted so far — getting that wrong would drift further out of
  sync with every sentence.
* Kokoro emits 24 kHz float samples, not an MP3. The pipeline stores narration
  as MP3, so the samples go through a WAV in the temp directory and FFmpeg does
  the encode.

``import kokoro`` below resolves to the installed package, not this module:
Python 3 imports are absolute, and ``app/tts`` is never on ``sys.path``.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode
from app.tts.base import (
    ProviderStatus,
    SynthesisRequest,
    SynthesisResult,
    Voice,
    WordTiming,
)
from app.tts.kokoro_catalog import (
    DEFAULT_VOICE,
    LANGUAGES,
    MAX_SPEED,
    MIN_SPEED,
    REPO_ID,
    SAMPLE_RATE,
    VOICES,
    find,
    is_known,
    lang_code_for,
)
from app.tts.pronunciation import apply_pronunciation, sanitize_for_tts

logger = logging.getLogger("evb.tts.kokoro")

#: Kokoro splits on this and then re-chunks to fit the model's phoneme budget.
#: Narration is sanitized to a single line, so the model's own chunking does
#: the real work — which is what we want: it groups whole sentences.
SPLIT_PATTERN = r"\n+"

PIP_INSTALL = "pip install 'kokoro>=0.9.4' soundfile"
BREW_INSTALL = "brew install espeak-ng"

#: One pipeline per (language, device). Loading one costs seconds and ~350 MB,
#: so they are reused for the whole process lifetime.
_pipelines: dict[tuple[str, str], Any] = {}
_pipeline_lock = threading.Lock()

#: Set once an MPS run has failed, so the next call goes straight to the CPU
#: instead of failing the same way again. Some torch builds are missing the FFT
#: kernels Kokoro's vocoder needs on Apple GPUs.
_mps_unusable = False


class KokoroProvider:
    name = "kokoro"

    # --- status -----------------------------------------------------------

    def status(self) -> ProviderStatus:
        installed = _package_available()
        cached = _model_is_cached()

        if not installed:
            return ProviderStatus(
                name=self.name,
                available=False,
                message=(
                    "Kurulu değil. Kurmak için: "
                    f"{PIP_INSTALL} — Seslendirme sekmesinde adımlar yazıyor."
                ),
                supports_rate=True,
                supports_pitch=False,
                supports_word_timings=True,
                offline=False,
            )

        if cached:
            message = "Hazır. Tamamen bu bilgisayarda çalışır, internet gerektirmez."
        else:
            message = (
                "Kurulu. Model ilk seslendirmede bir kez indirilecek "
                "(~350 MB, internet gerekir); sonrası tamamen çevrimdışıdır."
            )
        if not _espeak_available():
            message += " espeak-ng bulunamadı; bilimsel adlar için telaffuz sözlüğünü kullanın."

        return ProviderStatus(
            name=self.name,
            available=True,
            message=message,
            supports_rate=True,
            # Kokoro has no pitch control at all. Saying so keeps the Audio tab
            # from offering a slider that would silently do nothing.
            supports_pitch=False,
            supports_word_timings=True,
            offline=cached,
        )

    # --- voices -----------------------------------------------------------

    async def list_voices(self) -> list[Voice]:
        """The static catalogue — no network, no model load, always answers."""
        return [
            Voice(
                id=voice.id,
                name=voice.label,
                locale=voice.language.locale,
                gender=voice.gender,
                description=_voice_description(voice.grade, voice.training, voice.note),
            )
            for voice in VOICES
        ]

    # --- synthesis --------------------------------------------------------

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        settings = get_settings()
        text = sanitize_for_tts(apply_pronunciation(request.text, request.pronunciation))
        if not text:
            raise AppError(
                ErrorCode.MISSING_NARRATION,
                "Seslendirilecek metin yok.",
                suggestion="Bu sahneye metin yazın ya da sahneyi kapatın.",
            )

        voice = (request.voice or "").strip()
        if not is_known(voice):
            raise AppError(
                ErrorCode.TTS_FAILED,
                f"'{voice or '(boş)'}' Kokoro'nun tanıdığı bir ses değil.",
                details=f"expected one of: {', '.join(v.id for v in VOICES)}",
                suggestion=(
                    "Seslendirme sekmesinden Kokoro seslerinden birini seçin; "
                    f"belgesel anlatımı için {DEFAULT_VOICE} iyi bir başlangıçtır."
                ),
            )

        speed = min(MAX_SPEED, max(MIN_SPEED, float(request.rate)))
        # request.pitch is deliberately ignored: the model exposes no pitch
        # control, and pretending otherwise would produce identical audio.

        temp_dir = settings.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = temp_dir / f"kokoro-{uuid4().hex}.wav"
        request.output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            timings: list[WordTiming] = await anyio.to_thread.run_sync(
                lambda: _synthesize_to_wav(text, voice, speed, wav_path, settings)
            )
            await _encode_mp3(wav_path, request.output_path, settings)
        finally:
            wav_path.unlink(missing_ok=True)

        logger.info(
            "kokoro synthesized %s with %s, %d word timings -> %s",
            f"{len(text)} chars",
            voice,
            len(timings),
            request.output_path.name,
        )
        return SynthesisResult(
            path=request.output_path,
            duration_seconds=0.0,  # the caller measures this with ffprobe
            voice=voice,
            provider=self.name,
            word_timings=timings,
        )


# --- blocking work, always called on a worker thread ------------------------


def _synthesize_to_wav(
    text: str, voice: str, speed: float, wav_path: Path, settings: Settings
) -> list[WordTiming]:
    """Run the model and write a mono 24 kHz WAV. Returns word timings."""
    global _mps_unusable

    lang_code = lang_code_for(voice)
    device = _resolve_device(settings)

    try:
        segments, timings = _run_pipeline(text, voice, speed, lang_code, device)
    except Exception as exc:  # noqa: BLE001 - retried on CPU below, or re-raised
        if device == "mps" and not _mps_unusable:
            _mps_unusable = True
            logger.warning("kokoro failed on MPS (%s); retrying on the CPU", exc)
            segments, timings = _run_pipeline(text, voice, speed, lang_code, "cpu")
        else:
            raise _as_app_error(exc) from exc

    if not segments:
        raise AppError(
            ErrorCode.TTS_FAILED,
            "Kokoro bu metin için ses üretmedi.",
            details=f"voice={voice} lang={lang_code} chars={len(text)}",
            suggestion=(
                "Metinde okunacak bir şey olduğundan emin olun; yalnızca noktalama "
                "içeren satırlar sese dönüşmez."
            ),
        )

    _write_wav(wav_path, segments)
    return timings


def _run_pipeline(
    text: str, voice: str, speed: float, lang_code: str, device: str
) -> tuple[list[Any], list[WordTiming]]:
    """Generate every chunk, concatenating audio and rebasing its timestamps."""
    pipeline = _get_pipeline(lang_code, device)

    segments: list[Any] = []
    timings: list[WordTiming] = []
    offset = 0.0

    for result in pipeline(text, voice=voice, speed=speed, split_pattern=SPLIT_PATTERN):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        chunk = _to_float_array(audio)
        if chunk.size == 0:
            continue

        # Kokoro reports each chunk's timestamps from that chunk's own zero, so
        # they only line up with the joined audio once shifted by everything
        # emitted before them.
        for token in getattr(result, "tokens", None) or []:
            start = getattr(token, "start_ts", None)
            end = getattr(token, "end_ts", None)
            word = (getattr(token, "text", "") or "").strip()
            if start is None or end is None or not word:
                continue
            timings.append(
                WordTiming(
                    word=word,
                    start_seconds=offset + float(start),
                    end_seconds=offset + float(end),
                )
            )

        segments.append(chunk)
        offset += chunk.size / SAMPLE_RATE

    return segments, timings


def _get_pipeline(lang_code: str, device: str) -> Any:  # noqa: ANN401 - KPipeline
    key = (lang_code, device)
    with _pipeline_lock:
        pipeline = _pipelines.get(key)
        if pipeline is not None:
            return pipeline

        from kokoro import KPipeline

        logger.info("loading Kokoro pipeline lang=%s device=%s", lang_code, device)
        pipeline = KPipeline(lang_code=lang_code, repo_id=REPO_ID, device=device)
        _pipelines[key] = pipeline
        return pipeline


def _resolve_device(settings: Settings) -> str:
    """Pick a torch device, honouring the Settings preference.

    ``auto`` never chooses MPS: several torch builds lack the FFT kernels
    Kokoro's vocoder calls, and at 82M parameters the CPU is comfortably faster
    than real time anyway. MPS stays available as an explicit opt-in.
    """
    # str(KokoroDevice.CPU) is "KokoroDevice.CPU", not "cpu" — read .value when
    # it is an enum member and accept a plain string when it is not.
    raw = getattr(settings.mutable, "kokoro_device", None)
    preference = str(getattr(raw, "value", raw) or "auto").lower()
    if preference == "mps" and _mps_unusable:
        return "cpu"
    if preference in {"cpu", "mps", "cuda"}:
        return preference

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 - a torch that cannot even be queried means CPU
        pass
    return "cpu"


# --- audio helpers ----------------------------------------------------------


def _to_float_array(audio: Any) -> Any:  # noqa: ANN401 - torch tensor or ndarray
    import numpy as np

    if hasattr(audio, "detach"):  # torch tensor
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype="float32").reshape(-1)


def _write_wav(path: Path, segments: list[Any]) -> None:
    import numpy as np

    joined = np.concatenate(segments) if len(segments) > 1 else segments[0]
    # Clip before scaling: a sample slightly over 1.0 would wrap to full-scale
    # negative and land in the render as a click.
    pcm = (np.clip(joined, -1.0, 1.0) * 32767.0).astype("<i2")

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


async def _encode_mp3(wav_path: Path, output_path: Path, settings: Settings) -> None:
    """Transcode to the MP3 the narration cache expects."""
    from app.render.ffmpeg import FFmpegRunner

    runner = FFmpegRunner(settings=settings)
    ffmpeg = settings.require_tool("ffmpeg")
    try:
        await runner.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(wav_path),
                "-c:a", "libmp3lame", "-q:a", "2", "-ac", "1", "-ar", "48000",
                str(output_path),
            ],
            stage="kokoro-encode",
        )
    except AppError as exc:
        output_path.unlink(missing_ok=True)
        raise AppError(
            ErrorCode.TTS_FAILED,
            "Kokoro sesi MP3'e dönüştürülemedi.",
            details=exc.details or str(exc),
            suggestion=(
                "FFmpeg'in libmp3lame kodlayıcısıyla kurulu olduğundan emin olun "
                "(macOS'ta: brew install ffmpeg)."
            ),
        ) from exc


# --- environment probing ----------------------------------------------------


def _package_available() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("kokoro") is not None
    except (ImportError, ValueError):
        return False


def _espeak_available() -> bool:
    """Whether misaki can fall back to espeak-ng for out-of-dictionary words.

    Recent misaki bundles the library through ``espeakng-loader``, so a missing
    binary is a warning rather than a failure.
    """
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return True
    from importlib.util import find_spec

    try:
        return find_spec("espeakng_loader") is not None
    except (ImportError, ValueError):
        return False


def _hf_cache_root() -> Path:
    for variable, suffix in (("HF_HUB_CACHE", ""), ("HF_HOME", "hub")):
        value = os.environ.get(variable)
        if value:
            return Path(value).expanduser() / suffix if suffix else Path(value).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "huggingface" / "hub"


def _model_is_cached() -> bool:
    """True once the weights are on disk, i.e. once synthesis is fully offline."""
    folder = _hf_cache_root() / f"models--{REPO_ID.replace('/', '--')}"
    snapshots = folder / "snapshots"
    if not snapshots.is_dir():
        return False
    return any(snapshot.is_dir() and any(snapshot.iterdir()) for snapshot in snapshots.iterdir())


def _voice_description(grade: str, training: str, note: str) -> str:
    parts = []
    if grade and grade != "—":
        parts.append(f"{grade} notu")
    if training and training != "—":
        parts.append(training)
    if note:
        parts.append(note)
    return " · ".join(parts)


def _as_app_error(exc: Exception) -> AppError:
    """Turn a model/runtime failure into something the Audio tab can act on."""
    if isinstance(exc, AppError):
        return exc

    text = str(exc).lower()
    if isinstance(exc, ImportError) or "no module named" in text:
        return AppError(
            ErrorCode.TTS_PROVIDER_UNAVAILABLE,
            "Kokoro kurulu değil ya da bağımlılıkları eksik.",
            details=f"{type(exc).__name__}: {exc}",
            suggestion=f"Sanal ortamda çalıştırın: {PIP_INSTALL}",
        )
    if any(marker in text for marker in ("connection", "network", "timed out", "resolve", "offline")):
        return AppError(
            ErrorCode.TTS_PROVIDER_UNAVAILABLE,
            "Kokoro modeli indirilemedi.",
            details=f"{type(exc).__name__}: {exc}",
            suggestion=(
                "Model yalnızca ilk kullanımda indirilir ve internet ister. "
                "Bağlantınızı kontrol edip tekrar deneyin."
            ),
        )
    return AppError(
        ErrorCode.TTS_FAILED,
        "Kokoro seslendirmesi başarısız oldu.",
        details=f"{type(exc).__name__}: {exc}",
        suggestion=(
            "Ayarlar'dan Kokoro cihazını 'cpu' yapıp tekrar deneyin. "
            "Sürerse ses kaynağını Edge'e alabilirsiniz."
        ),
    )


def language_summary() -> list[dict[str, object]]:
    """Language rows for the Audio tab's Kokoro panel."""
    return [
        {
            "code": language.code,
            "label": language.label,
            "locale": language.locale,
            "extraInstall": language.extra_install,
            "wordTimings": language.word_timings,
            "voiceCount": sum(1 for v in VOICES if v.lang_code == language.code),
        }
        for language in LANGUAGES.values()
    ]


def environment_report() -> dict[str, object]:
    """What is and is not set up on this machine, for the UI to render."""
    installed = _package_available()
    return {
        "installed": installed,
        "modelCached": _model_is_cached(),
        "espeakAvailable": _espeak_available(),
        "device": _resolve_device(get_settings()),
        "cacheDir": str(_hf_cache_root() / f"models--{REPO_ID.replace('/', '--')}"),
        "pipInstall": PIP_INSTALL,
        "espeakInstall": BREW_INSTALL,
        "repoId": REPO_ID,
        "sampleRate": SAMPLE_RATE,
        "defaultVoice": DEFAULT_VOICE,
        "torchVersion": _torch_version() if installed else "",
    }


def _torch_version() -> str:
    try:
        import torch

        return str(torch.__version__)
    except Exception:  # noqa: BLE001 - reporting only
        return ""


def describe_voice(voice_id: str) -> dict[str, object] | None:
    voice = find(voice_id)
    if voice is None:
        return None
    return {
        "id": voice.id,
        "label": voice.label,
        "gender": voice.gender,
        "grade": voice.grade,
        "training": voice.training,
        "note": voice.note,
        "langCode": voice.lang_code,
        "language": voice.language.label,
        "locale": voice.language.locale,
        "wordTimings": voice.language.word_timings,
    }
