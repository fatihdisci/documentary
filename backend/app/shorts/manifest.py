"""The render manifest: where a Short learns what happened inside an MP4.

A finished long render is a single opaque file. Cutting a section out of it by
guessing from the container duration would be wrong the moment a transition
overlaps two sections — so the pipeline writes this manifest next to the export,
recording the absolute time of every section *as rendered*.

Two properties matter:

* **Immutable and versioned.** The manifest is written once, next to the MP4 it
  describes, and carries a ``schemaVersion``. It is never rewritten in place.
* **Bound to one exact file.** It records the export's size, SHA-256 and ffprobe
  summary. Before any Short is cut, the file on disk is checked against those
  values; a mismatch is a ``stale_render`` error, never a silent bad cut.

``safeStartSeconds``/``safeEndSeconds`` are the payload the Shorts feature
actually cuts on. A transition of duration ``d`` between two sections *overlaps*
them, so the frames in that window belong to both. The safe range excludes the
overlap at each end, which is what stops a non-contiguous selection from
carrying half a dissolve into an unrelated neighbour.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field, ValidationError as PydanticValidationError

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, NotFoundError, ValidationError
from app.models.base import CamelModel
from app.models.enums import QualityPreset
from app.models.project import Project, Scene, Section
from app.render.codecs import RenderProfile
from app.shorts.cues import CUE_SIDECAR_SCHEMA_VERSION, CueSidecarRef
from app.timing.probe import probe_video
from app.timing.schedule import Timeline

logger = logging.getLogger("evb.shorts.manifest")

#: Bumped whenever the meaning of a field changes. Readers refuse anything newer
#: and keep accepting every older version they know about.
#:
#: v2 added the optional ``shortsSource`` package (clean master + cue side-car).
#: Every v2 field is optional with a default, so a v1 manifest written before the
#: feature existed still validates and still cuts legacy Shorts exactly as it did
#: — it simply reports that Shorts-native captions are unavailable.
MANIFEST_SCHEMA_VERSION = 2

#: The oldest manifest this build can still read.
MIN_MANIFEST_SCHEMA_VERSION = 1

MANIFEST_SUFFIX = "-manifest.json"

#: How much the file on disk may differ from the recorded duration before the
#: manifest is considered to describe a different file.
DURATION_MATCH_TOLERANCE = 0.5


class ManifestSource(CamelModel):
    """The exact file this manifest describes."""

    filename: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    duration_seconds: float
    codec: str
    pix_fmt: str
    avg_frame_rate: str
    has_audio: bool
    audio_codec: str | None = None
    audio_sample_rate: int | None = None


class ManifestProfile(CamelModel):
    """Geometry and codecs the long render actually targeted."""

    width: int
    height: int
    fps: int
    quality: str
    video_codec: str = "h264"
    audio_codec: str = "aac"
    audio_sample_rate: int = 48_000


class ManifestEntry(CamelModel):
    """One section, placed on the finished video's absolute timeline."""

    unit_id: str
    kind: str  # "intro" | "scene" | "outro"
    #: What the user sees: intro is 0, active scenes are 1..N, outro is N+1.
    number: int
    title: str

    start_seconds: float
    end_seconds: float
    duration_seconds: float

    transition_to_next: str
    transition_duration_seconds: float
    #: Duration of the transition overlapping the *start* of this section.
    transition_from_previous_seconds: float

    #: The window a Short may cut when this section is taken on its own: the
    #: section minus any transition overlap it shares with a neighbour.
    safe_start_seconds: float
    safe_end_seconds: float

    #: Fade-up from black at the head of this section, if any. Reported so the
    #: UI can warn about a Short that would open on black; not clamped, because
    #: the fade is part of the picture the user rendered.
    fade_in_seconds: float = 0.0

    @property
    def safe_duration_seconds(self) -> float:
        return max(0.0, self.safe_end_seconds - self.safe_start_seconds)


#: Bumped when the *contents* of a Shorts source package change meaning. Kept
#: separate from the manifest version so a package can evolve without forcing a
#: manifest migration, and so a reader can reject just this part.
SHORTS_SOURCE_PACKAGE_VERSION = 1

#: How the clean master came to exist.
#:   ``primary-export``  the render had no burned-in subtitles, so the normal
#:                       export is already caption-free and is the clean master.
#:                       Costs nothing extra and is bit-identical to the export.
#:   ``dedicated-pass``  subtitles were burned in, so a second subtitle-free
#:                       encode of the same timeline was produced alongside.
CLEAN_MASTER_FROM_PRIMARY_EXPORT = "primary-export"
CLEAN_MASTER_FROM_DEDICATED_PASS = "dedicated-pass"


class ShortsSourcePackage(CamelModel):
    """The Shorts-ready source bound to one finished render.

    Immutable, and never inferred from a filename: a Short only uses a clean
    master this package explicitly names, whose checksum still matches, paired
    with the cue side-car recorded here. Anything else is a stale-source error
    the user is told how to fix — it is never a silent fall back to the captioned
    export, which would double-caption the Short.
    """

    package_version: int = SHORTS_SOURCE_PACKAGE_VERSION
    #: Relative to ``exports/`` — the package lives in ``exports/shorts-source/``.
    directory: str = "shorts-source"
    #: Full ffprobe description of the clean master, in the same shape as the
    #: normal export's, so both go through the same verification code.
    clean_master: ManifestSource
    origin: str = CLEAN_MASTER_FROM_DEDICATED_PASS
    #: Geometry and codecs the clean master targeted. Must equal the export's.
    profile: ManifestProfile
    cue_sidecar: CueSidecarRef
    #: Restated here so a mismatch is caught even if the two files were shuffled.
    render_job_id: str = ""
    project_snapshot_sha256: str = ""
    #: Checksum of the captioned export this clean master is the twin of.
    paired_export_sha256: str = ""
    written_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def has_burned_in_subtitles(self) -> bool:
        """Always false. A clean master with captions in it is a contradiction."""
        return False


class RenderManifest(CamelModel):
    """Everything a Short needs to know about a finished long render."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    render_job_id: str = ""
    project_slug: str
    #: SHA-256 of the project snapshot this render was produced from.
    project_snapshot_sha256: str
    source: ManifestSource
    profile: ManifestProfile
    total_duration_seconds: float
    #: Absolute time the closing fade-to-black begins, or the total duration if
    #: there is none. Nothing after this is worth putting in a Short.
    closing_fade_start_seconds: float
    entries: list[ManifestEntry] = Field(default_factory=list)
    written_at: datetime

    #: Whether the *normal* export has captions burned into the picture. Recorded
    #: so a Short can say why native captions are unavailable without guessing.
    #: Absent in v1 manifests, where it defaults to the historical behaviour:
    #: burn-in was on by default, and either way those pixels cannot be undone.
    source_has_burned_in_subtitles: bool = True
    #: Present only when the render prepared a Shorts-ready clean master. ``None``
    #: on every v1 manifest and on any render that opted out.
    shorts_source: ShortsSourcePackage | None = None

    def entry(self, unit_id: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.unit_id == unit_id), None)

    @property
    def supports_native_captions(self) -> bool:
        """Whether this render *claims* a usable Shorts source package.

        A claim only. The files it names are verified before anything is cut.
        """
        return (
            self.shorts_source is not None
            and self.shorts_source.package_version <= SHORTS_SOURCE_PACKAGE_VERSION
            and self.shorts_source.cue_sidecar.schema_version <= CUE_SIDECAR_SCHEMA_VERSION
        )


# --- writing ---------------------------------------------------------------


def manifest_path_for(video: Path) -> Path:
    """Where the manifest for ``video`` lives. Always beside the MP4."""
    return video.with_name(f"{video.stem}{MANIFEST_SUFFIX}")


def describe_file(
    video: Path, *, checksum: str = "", settings: Settings | None = None
) -> ManifestSource:
    """ffprobe one video and record everything a later check will compare."""
    info = probe_video(video, settings=settings or get_settings())
    return ManifestSource(
        filename=video.name,
        size_bytes=video.stat().st_size,
        sha256=checksum or sha256_file(video),
        width=info.width,
        height=info.height,
        duration_seconds=round(info.duration_seconds, 4),
        codec=info.codec,
        pix_fmt=info.pix_fmt,
        avg_frame_rate=info.avg_frame_rate,
        has_audio=info.has_audio,
        audio_codec=info.audio_codec,
        audio_sample_rate=info.audio_sample_rate,
    )


def build_manifest(
    video: Path,
    *,
    project: Project,
    timeline: Timeline,
    profile: RenderProfile,
    quality: QualityPreset,
    checksum: str,
    job_id: str = "",
    settings: Settings | None = None,
    shorts_source: ShortsSourcePackage | None = None,
) -> RenderManifest:
    """Describe ``video`` and the timeline that produced it."""
    active = settings or get_settings()
    info = probe_video(video, settings=active)
    closing_fade_start = _closing_fade_start(project, timeline)

    entries: list[ManifestEntry] = []
    scene_count = len(timeline.scene_entries)

    for position, entry in enumerate(timeline.entries):
        unit = _unit_for(project, entry.unit_id)
        incoming = (
            timeline.entries[position - 1].transition_duration if position > 0 else 0.0
        )
        outgoing = entry.transition_duration

        safe_start = entry.start_seconds + incoming
        safe_end = entry.end_seconds - outgoing
        if position == len(timeline.entries) - 1:
            # The tail hold and closing fade sit after the last section. Never
            # let a Short end on a frozen frame fading to black.
            safe_end = min(safe_end, closing_fade_start)
        # A pathological transition could invert the window; keep it sane.
        safe_end = max(safe_start, safe_end)

        entries.append(
            ManifestEntry(
                unit_id=entry.unit_id,
                kind=entry.kind,
                number=_display_number(entry.kind, entry.index, scene_count),
                title=_title_for(entry.kind, entry.index, unit),
                start_seconds=round(entry.start_seconds, 4),
                end_seconds=round(entry.end_seconds, 4),
                duration_seconds=round(entry.duration_seconds, 4),
                transition_to_next=entry.transition.value,
                transition_duration_seconds=round(outgoing, 4),
                transition_from_previous_seconds=round(incoming, 4),
                safe_start_seconds=round(safe_start, 4),
                safe_end_seconds=round(safe_end, 4),
                fade_in_seconds=round(
                    getattr(unit, "fade_from_black_seconds", 0.0) or 0.0, 4
                ),
            )
        )

    return RenderManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        render_job_id=job_id,
        project_slug=project.slug,
        project_snapshot_sha256=hashlib.sha256(
            project.model_dump_json().encode("utf-8")
        ).hexdigest(),
        source=ManifestSource(
            filename=video.name,
            size_bytes=video.stat().st_size,
            sha256=checksum or sha256_file(video),
            width=info.width,
            height=info.height,
            duration_seconds=round(info.duration_seconds, 4),
            codec=info.codec,
            pix_fmt=info.pix_fmt,
            avg_frame_rate=info.avg_frame_rate,
            has_audio=info.has_audio,
            audio_codec=info.audio_codec,
            audio_sample_rate=info.audio_sample_rate,
        ),
        profile=ManifestProfile(
            width=profile.width,
            height=profile.height,
            fps=profile.fps,
            quality=quality.value,
        ),
        total_duration_seconds=round(timeline.total_duration_seconds, 4),
        closing_fade_start_seconds=round(closing_fade_start, 4),
        entries=entries,
        written_at=datetime.now(timezone.utc),
        source_has_burned_in_subtitles=bool(project.subtitles.burn_in and timeline.cues),
        shorts_source=shorts_source,
    )


def write_render_manifest(
    video: Path,
    *,
    project: Project,
    timeline: Timeline,
    profile: RenderProfile,
    quality: QualityPreset,
    checksum: str,
    job_id: str = "",
    settings: Settings | None = None,
    shorts_source: ShortsSourcePackage | None = None,
) -> Path:
    """Build and atomically write the manifest for a finished render."""
    manifest = build_manifest(
        video,
        project=project,
        timeline=timeline,
        profile=profile,
        quality=quality,
        checksum=checksum,
        job_id=job_id,
        settings=settings,
        shorts_source=shorts_source,
    )
    target = manifest_path_for(video)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2), "utf-8")
    tmp.replace(target)
    logger.info("wrote render manifest %s (%d sections)", target.name, len(manifest.entries))
    return target


# --- reading and verifying --------------------------------------------------


def load_manifest(path: Path) -> RenderManifest:
    """Read a manifest, refusing anything unreadable or from a newer schema."""
    if not path.is_file():
        raise NotFoundError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"'{path.name.replace(MANIFEST_SUFFIX, '.mp4')}' dosyasının yanında bölüm "
            "bilgileri bulunamadı.",
            details=f"beklenen konum: {path}",
        )
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValidationError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"'{path.name}' dosyası okunamadı.",
            details=str(exc),
        ) from exc

    version = raw.get("schemaVersion", raw.get("schema_version"))
    if isinstance(version, int) and version > MANIFEST_SCHEMA_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"'{path.name}' dosyası uygulamanın daha yeni bir sürümüyle yazılmış.",
            details=(
                f"manifest schemaVersion={version}, "
                f"supported={MIN_MANIFEST_SCHEMA_VERSION}..{MANIFEST_SCHEMA_VERSION}"
            ),
        )

    try:
        return RenderManifest.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(
            ErrorCode.SHORT_MANIFEST_MISSING,
            f"'{path.name}' dosyasının biçimi beklenenden farklı.",
            details=str(exc)[:2000],
            suggestion=(
                "Uzun videoyu yeniden oluşturun; yeni video kendi bölüm bilgilerini yazar."
            ),
        ) from exc


def verify_source(
    manifest: RenderManifest,
    video: Path,
    *,
    settings: Settings | None = None,
    check_checksum: bool = True,
) -> None:
    """Confirm the file on disk is still the one the manifest describes.

    Raises ``stale_render`` for anything that would make a cut meaningless: the
    export deleted, re-encoded, truncated, or swapped for a different render.
    """
    if not video.is_file():
        raise NotFoundError(
            ErrorCode.STALE_RENDER,
            f"'{manifest.source.filename}' videosu artık bu projenin klasöründe yok.",
            details=f"beklenen konum: {video}",
            source_filename=manifest.source.filename,
        )

    size = video.stat().st_size
    if size != manifest.source.size_bytes:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' oluşturulduğundan beri değişmiş; bölüm bilgileri artık "
            "geçerli değil.",
            details=(
                f"kayıtlı boyut {manifest.source.size_bytes} bayt, "
                f"diskteki dosya {size} bayt"
            ),
            source_filename=video.name,
        )

    if check_checksum:
        digest = sha256_file(video)
        if digest != manifest.source.sha256:
            raise ValidationError(
                ErrorCode.STALE_RENDER,
                f"'{video.name}' artık kaydedildiği hâliyle aynı değil.",
                details=(
                    f"kayıtlı sha256 {manifest.source.sha256}\n"
                    f"dosya    sha256 {digest}"
                ),
                source_filename=video.name,
            )

    try:
        info = probe_video(video, settings=settings or get_settings())
    except AppError as exc:
        # An MP4 that FFmpeg cannot read is stale for our purposes, whatever the
        # underlying reason: truncated, half-written, or not really a video.
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' açılamadı, bu yüzden ondan kısa video kesilemez.",
            details=exc.details or exc.message,
            suggestion=(
                "Dosya bozuk ya da eksik. Uzun videoyu yeniden oluşturun ve kısa videoyu "
                "yeni dosyadan kesin."
            ),
            source_filename=video.name,
        ) from exc

    if info.width <= 0 or info.height <= 0:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' içinde okunabilir bir görüntü yok.",
            details=f"bulunan boyut: {info.width}x{info.height}",
            source_filename=video.name,
        )
    if not info.has_audio:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' içinde ses yok; bundan kesilecek kısa video sessiz olurdu.",
            details="dosyada ses kanalı bulunamadı",
            suggestion=(
                "Uzun videoyu seslendirme ya da müzik açıkken yeniden oluşturun, sonra kısa "
                "videoyu ondan kesin."
            ),
            source_filename=video.name,
        )
    if abs(info.duration_seconds - manifest.source.duration_seconds) > DURATION_MATCH_TOLERANCE:
        raise ValidationError(
            ErrorCode.STALE_RENDER,
            f"'{video.name}' {info.duration_seconds:.2f} saniye ama kayıtlarda "
            f"{manifest.source.duration_seconds:.2f} saniye yazıyor.",
            details="dosya oluşturulduktan sonra yeniden kaydedilmiş, kırpılmış ya da "
                    "değiştirilmiş",
            source_filename=video.name,
        )


def verify_clean_master(
    manifest: RenderManifest,
    clean_master: Path,
    *,
    settings: Settings | None = None,
    check_checksum: bool = True,
    probe: bool = True,
) -> ShortsSourcePackage:
    """Confirm the clean master is exactly the one this render recorded.

    Deliberately strict, and deliberately fatal. If any of this does not hold,
    the only safe outcomes are "use the burned-in source" or "re-render" — never
    "carry on with the captioned export", which would put two sets of captions on
    the same Short.

    ``check_checksum`` and ``probe`` are the two expensive steps, and both are
    skipped when this runs for every card in the source picker: hashing and
    ffprobing every render on every page load would be absurd. Both always run
    before a job is queued, which is the only point at which it matters.
    """
    package = manifest.shorts_source
    if package is None:
        raise ValidationError(
            ErrorCode.SHORT_CAPTIONS_UNAVAILABLE,
            "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak için "
            "uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden oluşturun.",
            details=(
                f"kayıt sürümü {manifest.schema_version}, altyazısız kopya bilgisi yok"
            ),
            source_filename=manifest.source.filename,
        )

    if package.package_version > SHORTS_SOURCE_PACKAGE_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "Bu videonun kısa video verileri uygulamanın daha yeni bir sürümüyle yazılmış.",
            details=(
                f"package version={package.package_version}, "
                f"supported={SHORTS_SOURCE_PACKAGE_VERSION}"
            ),
        )
    if package.cue_sidecar.schema_version > CUE_SIDECAR_SCHEMA_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "Bu videonun altyazı verisi uygulamanın daha yeni bir sürümüyle yazılmış.",
            details=(
                f"side-car schemaVersion={package.cue_sidecar.schema_version}, "
                f"supported={CUE_SIDECAR_SCHEMA_VERSION}"
            ),
        )

    # Identity: the package must belong to *this* render, not a neighbour's.
    if package.render_job_id and manifest.render_job_id and (
        package.render_job_id != manifest.render_job_id
    ):
        raise ValidationError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            "Diskteki altyazısız kopya başka bir videoya ait.",
            details=(
                f"kopya: {package.render_job_id!r}, video: {manifest.render_job_id!r}"
            ),
            source_filename=manifest.source.filename,
        )
    if package.project_snapshot_sha256 and (
        package.project_snapshot_sha256 != manifest.project_snapshot_sha256
    ):
        raise ValidationError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            "Altyazısız kopya, projenin başka bir hâlinden oluşturulmuş.",
            details=(
                f"kopya {package.project_snapshot_sha256[:16]}, "
                f"video {manifest.project_snapshot_sha256[:16]}"
            ),
            source_filename=manifest.source.filename,
        )

    expected = package.clean_master
    if not clean_master.is_file():
        raise NotFoundError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            f"Bu videonun altyazısız kopyası ('{expected.filename}') artık diskte yok.",
            details=f"beklenen konum: {clean_master}",
            suggestion=(
                "Uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden oluşturun. "
                "Ya da bu kısa videoyu videodaki mevcut altyazıyla oluşturun."
            ),
            source_filename=expected.filename,
        )

    size = clean_master.stat().st_size
    if size != expected.size_bytes:
        raise ValidationError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            f"Altyazısız kopya ('{expected.filename}') oluşturulduğundan beri değişmiş.",
            details=(
                f"kayıtlı boyut {expected.size_bytes} bayt, "
                f"diskteki dosya {size} bayt"
            ),
            source_filename=expected.filename,
        )

    if check_checksum:
        digest = sha256_file(clean_master)
        if digest != expected.sha256:
            raise ValidationError(
                ErrorCode.SHORT_CLEAN_SOURCE_STALE,
                f"Altyazısız kopya ('{expected.filename}') artık ait olduğu videoyla "
                "uyuşmuyor.",
                details=f"kayıtlı sha256 {expected.sha256}\ndosya    sha256 {digest}",
                source_filename=expected.filename,
            )

    if not probe:
        return package

    try:
        info = probe_video(clean_master, settings=settings or get_settings())
    except AppError as exc:
        raise ValidationError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            f"Altyazısız kopya ('{expected.filename}') açılamadı.",
            details=exc.details or exc.message,
            suggestion="Uzun videoyu yeniden oluşturun ve kısa videoyu yeni dosyadan kesin.",
            source_filename=expected.filename,
        ) from exc

    # Timeline identity: a clean master that is not the same length, shape or
    # frame rate as the export is not the same cut, and every section boundary in
    # the manifest would land in the wrong place.
    mismatches: list[str] = []
    if (info.width, info.height) != (manifest.source.width, manifest.source.height):
        mismatches.append(
            f"boyut {info.width}x{info.height}, "
            f"video {manifest.source.width}x{manifest.source.height}"
        )
    if info.avg_frame_rate != manifest.source.avg_frame_rate:
        mismatches.append(
            f"kare hızı {info.avg_frame_rate}, video {manifest.source.avg_frame_rate}"
        )
    if package.profile.fps != manifest.profile.fps:
        mismatches.append(f"kayıtlı fps {package.profile.fps}, video {manifest.profile.fps}")
    if abs(info.duration_seconds - manifest.source.duration_seconds) > DURATION_MATCH_TOLERANCE:
        mismatches.append(
            f"süre {info.duration_seconds:.2f} sn, video "
            f"{manifest.source.duration_seconds:.2f} sn"
        )
    if not info.has_audio:
        mismatches.append("ses kanalı yok")

    if mismatches:
        raise ValidationError(
            ErrorCode.SHORT_CLEAN_SOURCE_STALE,
            f"Altyazısız kopya ('{expected.filename}') ait olduğu videoyla uyuşmuyor; bölüm "
            "bilgileri artık geçerli değil.",
            details="\n".join(mismatches),
            suggestion="Uzun videoyu yeniden oluşturun ve kısa videoyu yeni dosyadan kesin.",
            source_filename=expected.filename,
        )

    return package


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- helpers ---------------------------------------------------------------


def _display_number(kind: str, scene_index: int, scene_count: int) -> int:
    if kind == "intro":
        return 0
    if kind == "outro":
        return scene_count + 1
    return scene_index + 1


def _title_for(kind: str, scene_index: int, unit: Scene | Section | None) -> str:
    title = (getattr(unit, "title", "") or "").strip()
    if title:
        return title[:200]
    if kind == "intro":
        return "Intro"
    if kind == "outro":
        return "Outro"
    return f"Scene {scene_index + 1}"


def _unit_for(project: Project, unit_id: str) -> Scene | Section | None:
    if unit_id == "intro":
        return project.intro
    if unit_id == "outro":
        return project.outro
    return project.scene_by_id(unit_id)


def _closing_fade_start(project: Project, timeline: Timeline) -> float:
    """When the final fade-to-black begins, mirroring the assemble stage.

    The pipeline pads the last frame through the audio tail and fades out over
    it. That maths lives in ``RenderPipeline._assemble``; reproducing it here
    keeps the manifest honest about which frames are still real picture.
    """
    total = timeline.total_duration_seconds
    tail = project.video.audio_tail_seconds
    if tail <= 0:
        return total

    last = timeline.entries[-1]
    unit = _unit_for(project, last.unit_id)
    configured = (
        unit.fade_to_black_seconds
        if isinstance(unit, Section) and unit.fade_to_black_seconds > 0
        else min(tail, 1.2)
    )
    fade_length = min(configured, total)
    return max(0.0, total - fade_length)
