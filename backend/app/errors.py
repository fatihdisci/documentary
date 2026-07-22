"""Error taxonomy for Extinct Video Builder.

Every failure surfaced to the user carries four things: a human-readable message,
a technical detail block, a suggested fix, and where to find the relevant log.
"Something went wrong" is never an acceptable payload.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from app.models.base import CamelModel


class ErrorCode(str, Enum):
    """Stable machine-readable codes. The frontend switches on these."""

    # Environment / tooling
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    FFMPEG_FAILED = "ffmpeg_failed"
    FFMPEG_CAPABILITY_MISSING = "ffmpeg_capability_missing"
    FONT_UNAVAILABLE = "font_unavailable"

    # Storage
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_EXISTS = "project_exists"
    PATH_TRAVERSAL = "path_traversal"
    PERMISSION_DENIED = "permission_denied"
    INSUFFICIENT_DISK_SPACE = "insufficient_disk_space"
    EXPORT_EXISTS = "export_exists"
    FILE_TOO_LARGE = "file_too_large"

    # Media
    UNSUPPORTED_IMAGE = "unsupported_image"
    CORRUPT_IMAGE = "corrupt_image"
    IMAGE_TOO_SMALL = "image_too_small"
    MISSING_IMAGE = "missing_image"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    CORRUPT_AUDIO = "corrupt_audio"

    # Content / schema
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION = "schema_validation"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"

    # TTS
    TTS_PROVIDER_UNAVAILABLE = "tts_provider_unavailable"
    TTS_TIMEOUT = "tts_timeout"
    TTS_FAILED = "tts_failed"
    TTS_INVALID_API_KEY = "tts_invalid_api_key"
    TTS_QUOTA_EXCEEDED = "tts_quota_exceeded"
    MISSING_NARRATION = "missing_narration"
    MISSING_AUDIO = "missing_audio"

    # Timing / render
    INVALID_DURATION = "invalid_duration"
    INVALID_TRANSITION = "invalid_transition"
    RENDER_CANCELLED = "render_cancelled"
    RENDER_FAILED = "render_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    JOB_NOT_FOUND = "job_not_found"

    # Shorts. A Short is only ever cut from a finished long render, so most of
    # these describe the source no longer being what the manifest recorded.
    SHORT_SOURCE_NOT_READY = "short_source_not_ready"
    SHORT_MANIFEST_MISSING = "short_manifest_missing"
    STALE_RENDER = "stale_render"
    SHORT_INVALID_SELECTION = "short_invalid_selection"
    SHORT_INVALID_TRIM = "short_invalid_trim"
    SHORT_TOO_LONG = "short_too_long"
    SHORT_JOB_NOT_FOUND = "short_job_not_found"
    SHORT_NOT_FOUND = "short_not_found"
    #: The render has no Shorts-ready clean master, so its captions exist only as
    #: burned-in pixels and cannot be replaced with large Shorts captions.
    SHORT_CAPTIONS_UNAVAILABLE = "short_captions_unavailable"
    #: A clean master or its caption data is missing, altered or mismatched.
    #: Deliberately distinct from ``stale_render``: the *normal* export may be
    #: perfectly fine, and only the Shorts-captioned path is blocked.
    SHORT_CLEAN_SOURCE_STALE = "short_clean_source_stale"

    # Generic fallback (still requires a real message + fix)
    INTERNAL = "internal"


#: Default remediation advice per code. Callers may override with something
#: more specific, but there is always a non-empty suggestion.
_DEFAULT_FIXES: dict[ErrorCode, str] = {
    ErrorCode.FFMPEG_NOT_FOUND: (
        "FFmpeg kurulu değil. Terminalden `brew install ffmpeg` ile kurun ya da "
        "Ayarlar → Video motoru bölümünde konumunu yazın."
    ),
    ErrorCode.FFPROBE_NOT_FOUND: (
        "ffprobe, FFmpeg ile birlikte gelir. FFmpeg'i kurun ya da Ayarlar'da konumunu yazın."
    ),
    ErrorCode.FFMPEG_FAILED: (
        "Ayrıntılar için kayıt dosyasına bakın. Eksik bir özellikten söz ediyorsa "
        "Sistem kontrolü sayfasından FFmpeg'in neleri desteklediğini görebilirsiniz."
    ),
    ErrorCode.FFMPEG_CAPABILITY_MISSING: (
        "Bilgisayarınızdaki FFmpeg sürümünde gereken bir özellik yok. Sistem kontrolü "
        "sayfasından hangi özelliklerin bulunduğunu görebilirsiniz."
    ),
    ErrorCode.FONT_UNAVAILABLE: (
        "Görünüm ayarlarından başka bir yazı tipi seçin."
    ),
    ErrorCode.PROJECT_NOT_FOUND: "Proje listesini yenileyin; proje taşınmış ya da silinmiş olabilir.",
    ErrorCode.PROJECT_EXISTS: "Başka bir proje adı seçin.",
    ErrorCode.PATH_TRAVERSAL: (
        "Bu dosya yolu proje klasörünün dışını gösteriyor ve kabul edilmedi. Dosyayı "
        "uygulama üzerinden yeniden yükleyin."
    ),
    ErrorCode.PERMISSION_DENIED: (
        "Ayarlar'da tanımlı klasörün yazma izinlerini kontrol edin."
    ),
    ErrorCode.INSUFFICIENT_DISK_SPACE: (
        "Diskte yer açın, video kalitesini düşürün ya da Ayarlar'dan geçici dosya "
        "klasörünü daha geniş bir diske taşıyın."
    ),
    ErrorCode.EXPORT_EXISTS: (
        "Videolar kendiliğinden numaralanır. Bu hatayı görüyorsanız boş bir dosya adı "
        "bulunamamış demektir — eski videoları silin ya da projeyi yeniden adlandırın."
    ),
    ErrorCode.FILE_TOO_LARGE: "Dosyayı küçültün ya da Ayarlar'dan yükleme sınırını yükseltin.",
    ErrorCode.UNSUPPORTED_IMAGE: "PNG, JPEG veya WebP kullanın.",
    ErrorCode.CORRUPT_IMAGE: "Dosya açılamadı. Görseli yeniden kaydedip tekrar yükleyin.",
    ErrorCode.IMAGE_TOO_SMALL: (
        "En az 1280x720 boyutunda bir görsel kullanın. Daha küçük görseller, yakınlaşma "
        "hareketi uygulanınca gözle görülür şekilde bulanıklaşır."
    ),
    ErrorCode.MISSING_IMAGE: "Bu sahne için bir görsel yükleyin ya da başka bir görsel seçin.",
    ErrorCode.UNSUPPORTED_AUDIO: "WAV, MP3 veya M4A kullanın.",
    ErrorCode.CORRUPT_AUDIO: "Ses dosyası açılamadı. Yeniden kaydedip tekrar yükleyin.",
    ErrorCode.INVALID_JSON: "Dosyadaki yazım hatasını düzeltin. Hatanın yeri ayrıntılarda yazıyor.",
    ErrorCode.SCHEMA_VALIDATION: "Ayrıntılarda yazan alanları düzeltip yeniden yükleyin.",
    ErrorCode.UNSUPPORTED_SCHEMA_VERSION: (
        "Bu dosya uygulamanın daha yeni bir sürümüyle oluşturulmuş. Uygulamayı güncelleyin."
    ),
    ErrorCode.TTS_PROVIDER_UNAVAILABLE: (
        "İnternet bağlantınızı kontrol edin. Ya da ses kaynağını “kendi kayıtlarım” yapıp "
        "her sahne için hazır ses dosyası yükleyin — böylece internet gerekmez."
    ),
    ErrorCode.TTS_TIMEOUT: (
        "Tekrar deneyin. Sürekli oluyorsa başka bir ses kaynağı seçin ya da kendi ses "
        "kayıtlarınızı yükleyin."
    ),
    ErrorCode.TTS_FAILED: (
        "Servisin verdiği yanıt ayrıntılarda yazıyor. Sonra ilgili sahneleri tekrar seslendirin."
    ),
    ErrorCode.TTS_INVALID_API_KEY: "Ayarlar → Servis anahtarları bölümünden anahtarı yeniden girin.",
    ErrorCode.TTS_QUOTA_EXCEEDED: (
        "Kotanızın yenilenmesini bekleyin, ücretsiz olan Edge'e geçin ya da kendi ses "
        "kayıtlarınızı yükleyin."
    ),
    ErrorCode.MISSING_NARRATION: "Bu sahneye konuşma metni yazın ya da sahneyi kapatın.",
    ErrorCode.MISSING_AUDIO: (
        "Video oluşturmadan önce bu sahneyi seslendirin ya da bir ses dosyası yükleyin."
    ),
    ErrorCode.INVALID_DURATION: "Sahne süresini değiştirin ya da süre belirleme yöntemini değiştirin.",
    ErrorCode.INVALID_TRANSITION: (
        "Geçişi kısaltın ya da iki yanındaki sahneleri uzatın. Bir geçiş, kısa olan "
        "komşusunun %40'ından uzun olamaz."
    ),
    ErrorCode.RENDER_CANCELLED: "Hazır olduğunuzda yeniden başlatabilirsiniz.",
    ErrorCode.RENDER_FAILED: (
        "Kayıt dosyasında hangi aşamada durduğu yazıyor. Sebebini giderip tekrar deneyin."
    ),
    ErrorCode.OUTPUT_VALIDATION_FAILED: (
        "Video oluştu ama kontrolden geçemedi. Nelerin beklendiği ve ne bulunduğu "
        "ayrıntılarda yazıyor. Tekrar deneyin; yine olursa bunu bildirin."
    ),
    ErrorCode.JOB_NOT_FOUND: "Liste güncel olmayabilir. Geçmişi yenileyin.",
    ErrorCode.SHORT_SOURCE_NOT_READY: (
        "Kısa videolar bitmiş bir videodan kesilir. Tamamlanmış bir video seçin ya da "
        "önce uzun videoyu oluşturun."
    ),
    ErrorCode.SHORT_MANIFEST_MISSING: (
        "Bu video, kısa video özelliği eklenmeden önce oluşturulmuş; bölüm bilgisi yok. "
        "Uzun videoyu bir kez yeniden oluşturun, sonra kısa video kesebilirsiniz."
    ),
    ErrorCode.STALE_RENDER: (
        "Video dosyası, kaydedildiği hâlinden farklı. Uzun videoyu yeniden oluşturun ve "
        "kısa videoyu yeni dosyadan kesin."
    ),
    ErrorCode.SHORT_INVALID_SELECTION: (
        "En az bir bölüm seçin ve yalnızca seçtiğiniz videoda gerçekten bulunan bölümleri seçin."
    ),
    ErrorCode.SHORT_INVALID_TRIM: (
        "Kırpmayı bölümün izin verilen aralığında tutun; başlangıç bitişten önce olmalı."
    ),
    ErrorCode.SHORT_TOO_LONG: (
        "YouTube yalnızca üç dakikaya kadar olan videoları kısa video sayar. Bir bölümü "
        "çıkarın ya da seçiminizi kısaltın."
    ),
    ErrorCode.SHORT_JOB_NOT_FOUND: "Liste güncel olmayabilir. Kısa video sekmesini yenileyin.",
    ErrorCode.SHORT_NOT_FOUND: (
        "Bu kısa video artık diskte yok. Listeyi yenilemek için sekmeyi tekrar açın."
    ),
    ErrorCode.SHORT_CAPTIONS_UNAVAILABLE: (
        "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak için uzun "
        "videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden oluşturun. Ya da bu "
        "kısa videoyu videodaki mevcut altyazıyla oluşturun."
    ),
    ErrorCode.SHORT_CLEAN_SOURCE_STALE: (
        "Altyazısız kopya ya da altyazı verisi, ait olduğu videoyla artık uyuşmuyor. Uzun "
        "videoyu yeniden oluşturun ve kısa videoyu yeni dosyadan kesin."
    ),
    ErrorCode.INTERNAL: "Ayrıntılı bilgi için arka uç kayıt dosyasına bakın.",
}


class ErrorPayload(CamelModel):
    """The wire format for every error the API returns."""

    code: ErrorCode
    message: str = Field(description="Human-readable, specific, no jargon required to act on it.")
    details: str | None = Field(default=None, description="Technical detail: stderr, traceback, field errors.")
    suggestion: str = Field(description="What the user should do next.")
    log_path: str | None = Field(default=None, description="Absolute path to the most relevant log file.")
    context: dict[str, Any] = Field(default_factory=dict)


class AppError(Exception):
    """Base class for every deliberate failure in the application.

    Raising this (rather than a bare Exception) guarantees the user gets an
    actionable message instead of a 500.
    """

    http_status: int = 400

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: str | None = None,
        suggestion: str | None = None,
        log_path: str | None = None,
        http_status: int | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.suggestion = suggestion or _DEFAULT_FIXES.get(code, _DEFAULT_FIXES[ErrorCode.INTERNAL])
        self.log_path = log_path
        self.context = context
        if http_status is not None:
            self.http_status = http_status

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(
            code=self.code,
            message=self.message,
            details=self.details,
            suggestion=self.suggestion,
            log_path=self.log_path,
            context=self.context,
        )

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"[{self.code.value}] {self.message}"


class NotFoundError(AppError):
    http_status = 404


class ConflictError(AppError):
    http_status = 409


class ValidationError(AppError):
    http_status = 422


class EnvironmentError_(AppError):
    """Tooling/environment problem (FFmpeg missing, etc.). Not the user's data."""

    http_status = 503


class RenderError(AppError):
    http_status = 500
