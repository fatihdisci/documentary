"""Everything the Shorts API needs that is not a running FFmpeg process.

Source discovery deliberately reads **manifests on disk**, not the render job
history. A manifest is only ever written when a long render completes and its
output passes validation, so its presence is the proof that a source is usable —
and it keeps working after the job history has been pruned. Where a job record
does still exist it is cross-checked, so a render that somehow ended up
non-completed can never be offered as a source.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, NotFoundError, ValidationError
from app.models.enums import JobStatus
from app.render.ffmpeg import FFmpegRunner
from app.shorts.cues import CueSidecar, load_sidecar, rebase_cues
from app.shorts.encode import segment_cache_name
from app.shorts.manifest import (
    MANIFEST_SUFFIX,
    RenderManifest,
    load_manifest,
    manifest_path_for,
    verify_clean_master,
    verify_source,
)
from app.shorts.models import (
    CONTENT_ID_WARN_SECONDS,
    MAX_SHORT_SECONDS,
    MIN_CLIP_SECONDS,
    RECOMMENDED_MAX_SECONDS,
    RECOMMENDED_MIN_SECONDS,
    ShortArtifact,
    ShortCaptionMode,
    ShortCaptionSupport,
    ShortManifest,
    ShortPreviewFrame,
    ShortRecord,
    ShortRequest,
    ShortSourceRender,
    ShortSourceTimeline,
    ShortTimelineSection,
    ShortsPreflightResponse,
)
from app.shorts.plan import build_plan
from app.storage.layout import ProjectPaths
from app.storage.paths import safe_join
from app.storage.repository import ProjectRepository

logger = logging.getLogger("evb.shorts.service")

#: Identifiers arriving from the URL. Nothing outside this alphabet is ever
#: compared, let alone joined onto a path.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: Preview/poster frames are small on purpose: they exist to show the framing
#: and the cut points, not to be watched.
PREVIEW_FRAME_WIDTH = 480


class ShortsService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.repository = ProjectRepository(self.settings)

    # --- locating -------------------------------------------------------

    def paths_for(self, slug: str) -> ProjectPaths:
        self.repository.load(slug)  # 404s early if the project is gone
        return self.repository.paths_for(slug)

    # --- sources --------------------------------------------------------

    def list_sources(self, slug: str) -> list[ShortSourceRender]:
        """Completed long renders that still have a usable manifest and file."""
        paths = self.paths_for(slug)
        if not paths.exports.is_dir():
            return []

        statuses = self._render_job_statuses(slug)
        sources: list[ShortSourceRender] = []

        for manifest_path in sorted(paths.exports.glob(f"*{MANIFEST_SUFFIX}")):
            try:
                manifest = load_manifest(manifest_path)
            except AppError as exc:
                logger.info("skipping unusable manifest %s: %s", manifest_path.name, exc)
                continue

            render_id = self._render_id(manifest, manifest_path)
            status = statuses.get(manifest.render_job_id)
            if status is not None and status is not JobStatus.COMPLETED:
                # Belt and braces: a manifest is only written on success, but if
                # the job record disagrees, believe the job record.
                continue

            sources.append(
                self._describe(slug, paths, manifest, render_id, manifest_path)
            )

        sources.sort(key=lambda s: s.created_at, reverse=True)
        return sources

    def load_source(self, slug: str, render_id: str) -> tuple[RenderManifest, ShortSourceRender]:
        """Resolve a source id to its manifest, or fail with a clear error."""
        _require_safe_id(render_id, "render")
        paths = self.paths_for(slug)

        for manifest_path in sorted(paths.exports.glob(f"*{MANIFEST_SUFFIX}")):
            try:
                manifest = load_manifest(manifest_path)
            except AppError:
                continue
            if self._render_id(manifest, manifest_path) != render_id:
                continue
            return manifest, self._describe(slug, paths, manifest, render_id, manifest_path)

        raise NotFoundError(
            ErrorCode.SHORT_SOURCE_NOT_READY,
            f"Bu projede '{render_id}' numaralı tamamlanmış bir video bulunamadı.",
            details=f"aranan klasör: {paths.exports}",
            suggestion=(
                "Sekmeyi yenileyin. Video gerçekten yoksa uzun videoyu yeniden oluşturun — "
                "kısa video ancak tamamlanmış bir videodan kesilebilir."
            ),
        )

    # --- caption capability ---------------------------------------------

    def clean_master_path(self, paths: ProjectPaths, manifest: RenderManifest) -> Path | None:
        """Where this render's clean master lives, if it claims one.

        Read from the package's own recorded filename. Nothing here reconstructs
        a name from the export's: a Short only ever uses a file the manifest
        explicitly points at.
        """
        package = manifest.shorts_source
        if package is None:
            return None
        directory = paths.exports / package.directory if package.directory else paths.exports
        return safe_join(directory, package.clean_master.filename)

    def cue_sidecar_path(self, paths: ProjectPaths, manifest: RenderManifest) -> Path | None:
        package = manifest.shorts_source
        if package is None:
            return None
        directory = paths.exports / package.directory if package.directory else paths.exports
        return safe_join(directory, package.cue_sidecar.filename)

    def caption_support(
        self, paths: ProjectPaths, manifest: RenderManifest, *, deep: bool = False
    ) -> ShortCaptionSupport:
        """Whether this render can drive Shorts-native captions, and why not.

        ``deep`` runs the full checksum and ffprobe verification. The default is
        the cheap version — existence and recorded size — because this is called
        for every source card in the picker and hashing a 2 GB export per card
        would be absurd. The full check always runs before a job is queued.
        """
        burned = manifest.source_has_burned_in_subtitles
        package = manifest.shorts_source

        if package is None:
            return ShortCaptionSupport(
                native_available=False,
                source_has_burned_in_subtitles=burned,
                reason=(
                    "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı "
                    "kullanmak için uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken "
                    "yeniden oluşturun."
                    if burned
                    else "Bu video, büyük altyazı özelliği eklenmeden önce oluşturulmuş; "
                    "altyazı verisi yok. Uzun videoyu altyazısız kopya hazırlama seçeneği "
                    "açıkken yeniden oluşturun."
                ),
            )

        base = ShortCaptionSupport(
            source_has_burned_in_subtitles=burned,
            cue_count=package.cue_sidecar.cue_count,
            clean_master_filename=package.clean_master.filename,
            cue_sidecar_filename=package.cue_sidecar.filename,
            cue_schema_version=package.cue_sidecar.schema_version,
            clean_master_origin=package.origin,
        )

        if not manifest.supports_native_captions:
            return base.model_copy(
                update={
                    "reason": (
                        "Bu videonun kısa video verileri uygulamanın daha yeni bir sürümüyle "
                        "yazılmış ve burada okunamıyor. Uzun videoyu yeniden oluşturun."
                    )
                }
            )

        master = self.clean_master_path(paths, manifest)
        sidecar = self.cue_sidecar_path(paths, manifest)
        try:
            if master is None or sidecar is None:
                raise ValidationError(
                    ErrorCode.SHORT_CLEAN_SOURCE_STALE,
                    "Bu videonun altyazısız kopyası bulunamadı.",
                )
            verify_clean_master(
                manifest, master, settings=self.settings,
                check_checksum=deep, probe=deep,
            )
            load_sidecar(sidecar, package.cue_sidecar if deep else None)
        except AppError as exc:
            return base.model_copy(update={"reason": exc.message})

        if package.cue_sidecar.cue_count == 0:
            return base.model_copy(
                update={
                    "reason": (
                        "Bu videoda konuşma altyazısı yok, çizilecek bir şey de yok. Metin "
                        "ekleyip videoyu yeniden oluşturun."
                    )
                }
            )

        return base.model_copy(update={"native_available": True, "reason": None})

    def load_caption_cues(
        self, paths: ProjectPaths, manifest: RenderManifest
    ) -> CueSidecar:
        """Fully verified caption data for a render. Raises if anything is off."""
        master = self.clean_master_path(paths, manifest)
        sidecar = self.cue_sidecar_path(paths, manifest)
        if master is None or sidecar is None:
            raise ValidationError(
                ErrorCode.SHORT_CAPTIONS_UNAVAILABLE,
                "Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak "
                "için uzun videoyu, altyazısız kopya hazırlama seçeneği açıkken yeniden "
                "oluşturun.",
                details="bu videonun kayıtlarında altyazısız kopya bilgisi yok",
            )
        package = verify_clean_master(manifest, master, settings=self.settings)
        cues = load_sidecar(sidecar, package.cue_sidecar)
        if cues.clean_master_sha256 != package.clean_master.sha256:
            raise ValidationError(
                ErrorCode.SHORT_CLEAN_SOURCE_STALE,
                "Bu videonun altyazı verisi başka bir kopyaya ait.",
                details=(
                    f"altyazı dosyası: {cues.clean_master_sha256[:16]}, "
                    f"kayıtlı: {package.clean_master.sha256[:16]}"
                ),
                suggestion="Uzun videoyu yeniden oluşturun ve kısa videoyu ondan kesin.",
            )
        return cues

    def timeline(self, slug: str, render_id: str) -> ShortSourceTimeline:
        manifest, source = self.load_source(slug, render_id)
        return ShortSourceTimeline(
            source=source,
            fps=manifest.profile.fps,
            total_duration_seconds=manifest.total_duration_seconds,
            sections=[
                ShortTimelineSection(
                    unit_id=entry.unit_id,
                    kind=entry.kind,
                    number=entry.number,
                    title=entry.title,
                    start_seconds=entry.start_seconds,
                    end_seconds=entry.end_seconds,
                    duration_seconds=entry.duration_seconds,
                    safe_start_seconds=entry.safe_start_seconds,
                    safe_end_seconds=entry.safe_end_seconds,
                    safe_duration_seconds=round(entry.safe_duration_seconds, 4),
                    transition_to_next=entry.transition_to_next,
                    transition_duration_seconds=entry.transition_duration_seconds,
                    transition_from_previous_seconds=entry.transition_from_previous_seconds,
                    fade_in_seconds=entry.fade_in_seconds,
                )
                for entry in manifest.entries
            ],
        )

    # --- preflight ------------------------------------------------------

    def preflight(
        self,
        slug: str,
        request: ShortRequest,
        *,
        with_frames: bool = True,
    ) -> ShortsPreflightResponse:
        """Report exactly what would be built, and what would stop it.

        Never raises for a user mistake: a bad selection comes back as a
        blocking issue so the page can show every problem at once.
        """
        blocking: list[str] = []
        warnings: list[str] = []
        source: ShortSourceRender | None = None
        manifest: RenderManifest | None = None
        style = request.resolved_caption_style()

        try:
            manifest, source = self.load_source(slug, request.source_render_id)
        except AppError as exc:
            return ShortsPreflightResponse(
                ready=False,
                blocking_issues=[exc.message],
                warnings=[],
                caption_mode=request.caption_mode,
            )

        paths = self.paths_for(slug)
        video = safe_join(paths.exports, manifest.source.filename)
        support = source.captions
        try:
            # Cheap checks only here — hashing a 2 GB export on every keystroke
            # would be absurd. The full checksum is verified when the job runs.
            verify_source(manifest, video, settings=self.settings, check_checksum=False)
        except AppError as exc:
            return ShortsPreflightResponse(
                ready=False,
                blocking_issues=[exc.message],
                source=source,
                caption_mode=request.caption_mode,
                caption_support=support,
            )

        # Captions are validated *before* the plan, so a render that cannot do
        # native captions says exactly that rather than reporting some unrelated
        # trim problem first. There is deliberately no fallback to the burned-in
        # source: that would put two sets of captions on the same Short.
        if request.caption_mode.needs_clean_master and not support.native_available:
            blocking.append(
                support.reason
                or "Bu video, kısa video altyazılarıyla kullanılamıyor."
            )

        try:
            plan = build_plan(manifest, request)
        except AppError as exc:
            return ShortsPreflightResponse(
                ready=False,
                blocking_issues=[*blocking, exc.message],
                source=source,
                caption_mode=request.caption_mode,
                caption_style=style if request.caption_mode.needs_clean_master else None,
                caption_support=support,
            )

        warnings.extend(plan.warnings)
        total = plan.total_duration_seconds

        rendered_cues = 0
        if request.caption_mode is ShortCaptionMode.SHORTS_NATIVE and support.native_available:
            try:
                cues = self.load_caption_cues(paths, manifest)
                rendered_cues = len(rebase_cues(cues.cues, plan.groups))
            except AppError as exc:
                blocking.append(exc.message)
            else:
                if rendered_cues == 0:
                    warnings.append(
                        "Seçtiğiniz bölümlerin hiçbirinde konuşma yok, bu yüzden kısa videoda "
                        "altyazı olmayacak. Konuşması olan bir bölüm seçin ya da altyazıyı "
                        "kapatın."
                    )

        frames: list[ShortPreviewFrame] = []
        if with_frames:
            frames = self.preview_frames(slug, manifest, plan)

        cached = self.find_by_cache_key(slug, plan.cache_key)

        return ShortsPreflightResponse(
            ready=not blocking,
            blocking_issues=blocking,
            warnings=warnings,
            source=source,
            plan=plan,
            total_duration_seconds=total,
            within_recommended_band=(
                RECOMMENDED_MIN_SECONDS <= total <= RECOMMENDED_MAX_SECONDS
            ),
            exceeds_content_id_warning=total > CONTENT_ID_WARN_SECONDS,
            exceeds_maximum=total > MAX_SHORT_SECONDS,
            preview_frames=frames,
            cached_short_id=cached.short_id if cached else None,
            estimated_render_seconds=round(max(3.0, total * 0.45 + 2.0), 0),
            caption_mode=request.caption_mode,
            caption_style=style if request.caption_mode.needs_clean_master else None,
            caption_support=support,
            caption_cue_count=rendered_cues,
        )

    # --- preview frames -------------------------------------------------

    def preview_frames(
        self, slug: str, manifest: RenderManifest, plan
    ) -> list[ShortPreviewFrame]:  # noqa: ANN001 - ShortPlan, avoiding a cycle
        """One real frame per cut, taken from the source at the cut's first frame.

        Cached by source checksum and timestamp, so dragging a trim handle back
        to a value already seen costs nothing. Extraction failures are silent:
        a preview is an aid, never a gate.
        """
        paths = self.paths_for(slug)
        frames: list[ShortPreviewFrame] = []
        for group in plan.groups:
            path = self._frame(paths, manifest, group.start_seconds)
            if path is None:
                continue
            frames.append(
                ShortPreviewFrame(
                    group_index=group.index,
                    time_seconds=group.start_seconds,
                    url=f"/api/projects/{slug}/shorts/frames/{path.name}",
                )
            )
        return frames

    def source_poster(self, slug: str, render_id: str) -> Path:
        """A thumbnail for the source picker. Generated once, then cached."""
        manifest, _ = self.load_source(slug, render_id)
        paths = self.paths_for(slug)
        # A little way in, so an opening fade from black is not the thumbnail.
        at = min(2.0, max(0.0, manifest.total_duration_seconds * 0.1))
        path = self._frame(paths, manifest, at)
        if path is None:
            raise NotFoundError(
                ErrorCode.SHORT_SOURCE_NOT_READY,
                "Bu videodan küçük bir önizleme görüntüsü alınamadı.",
                details=f"{at:.2f}. saniyeden kare alınmaya çalışıldı",
                suggestion="Videoda sorun yok; yalnızca önizleme görüntüsü eksik.",
            )
        return path

    def frame_path(self, slug: str, filename: str) -> Path:
        paths = self.paths_for(slug)
        target = safe_join(paths.shorts_cache / "frames", filename)
        if not target.is_file():
            raise NotFoundError(
                ErrorCode.SHORT_NOT_FOUND,
                f"'{filename}' diye bir önizleme görüntüsü yok.",
                suggestion="Önizlemeyi yeniden oluşturmak için sekmeyi yenileyin.",
            )
        return target

    def _frame(self, paths: ProjectPaths, manifest: RenderManifest, at: float) -> Path | None:
        directory = paths.shorts_cache / "frames"
        target = directory / f"{manifest.source.sha256[:16]}-{int(round(at * 1000)):09d}.jpg"
        if target.is_file():
            return target

        video = safe_join(paths.exports, manifest.source.filename)
        if not video.is_file():
            return None

        directory.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial.jpg")
        runner = FFmpegRunner(self.settings)
        try:
            result = runner._run_sync(  # noqa: SLF001 - same module family as probe.py
                [
                    self.settings.require_tool("ffmpeg"),
                    "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                    "-ss", f"{max(0.0, at):.3f}",
                    "-i", str(video),
                    "-frames:v", "1",
                    "-vf", f"scale={PREVIEW_FRAME_WIDTH}:-2",
                    "-q:v", "4",
                    str(partial),
                ],
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001 - a preview must never fail a request
            logger.info("preview frame at %.2fs failed: %s", at, exc)
            return None

        if not result.ok or not partial.is_file():
            partial.unlink(missing_ok=True)
            return None
        partial.replace(target)
        return target

    # --- finished shorts ------------------------------------------------

    def list_shorts(self, slug: str) -> list[ShortRecord]:
        paths = self.paths_for(slug)
        records = [
            self._record(slug, paths, manifest, path)
            for manifest, path in self._short_manifests(paths)
        ]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def find_by_cache_key(self, slug: str, cache_key: str) -> ShortRecord | None:
        paths = self.paths_for(slug)
        for manifest, path in self._short_manifests(paths):
            if manifest.cache_key == cache_key:
                return self._record(slug, paths, manifest, path)
        return None

    def get_short(self, slug: str, short_id: str) -> tuple[ShortManifest, Path]:
        _require_safe_id(short_id, "short")
        paths = self.paths_for(slug)
        for manifest, path in self._short_manifests(paths):
            if manifest.short_id == short_id:
                return manifest, path
        raise NotFoundError(
            ErrorCode.SHORT_NOT_FOUND,
            f"Bu projede '{short_id}' numaralı bir kısa video bulunamadı.",
            details=f"aranan klasör: {paths.shorts_exports}",
        )

    def export_path(self, slug: str, filename: str) -> Path:
        paths = self.paths_for(slug)
        target = safe_join(paths.shorts_exports, filename)
        if not target.is_file():
            raise NotFoundError(
                ErrorCode.SHORT_NOT_FOUND,
                f"'{filename}' bu projenin kısa videoları arasında yok.",
                suggestion="Sekmeyi yenileyin; dosya silinmiş olabilir.",
            )
        return target

    def delete_short(self, slug: str, short_id: str) -> dict[str, object]:
        """Remove one Short and nothing else.

        Only files belonging to this Short go: its MP4, its manifest, its log,
        and any cached cut that no other Short still needs. The long render, the
        project and every other Short are untouched.
        """
        manifest, manifest_path = self.get_short(slug, short_id)
        paths = self.paths_for(slug)

        removed: list[str] = []
        video = safe_join(paths.shorts_exports, manifest.filename)
        for target in (video, manifest_path, video.with_name(f"{video.stem}.log")):
            if target.is_file():
                target.unlink()
                removed.append(target.name)

        cache_removed = self._prune_cuts(paths, manifest)
        work = paths.shorts_cache / "work" / (manifest.job_id or manifest.short_id)
        if work.is_dir():
            shutil.rmtree(work, ignore_errors=True)

        logger.info("deleted short %s (%d files, %d cached cuts)",
                    short_id, len(removed), cache_removed)
        return {"shortId": short_id, "removed": removed, "cacheEntriesRemoved": cache_removed}

    def _prune_cuts(self, paths: ProjectPaths, manifest: ShortManifest) -> int:
        """Drop cached cuts this Short was the last user of.

        Cuts are content-addressed, so two Shorts sharing a span share the file.
        Deleting one Short must not slow the other down, so only cuts nothing
        else still references are removed.
        """
        survivors: set[str] = set()
        for other, _ in self._short_manifests(paths):
            if other.short_id == manifest.short_id:
                continue
            survivors.update(_cut_names(other))

        removed = 0
        segments = paths.shorts_cache / "segments"
        for name in _cut_names(manifest) - survivors:
            candidate = segments / name
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        return removed

    # --- helpers --------------------------------------------------------

    def _short_manifests(self, paths: ProjectPaths) -> list[tuple[ShortManifest, Path]]:
        directory = paths.shorts_exports
        if not directory.is_dir():
            return []
        found: list[tuple[ShortManifest, Path]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                manifest = ShortManifest.model_validate_json(path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                logger.info("skipping unreadable short manifest %s: %s", path.name, exc)
                continue
            if not (directory / manifest.filename).is_file():
                continue
            found.append((manifest, path))
        return found

    def _record(
        self, slug: str, paths: ProjectPaths, manifest: ShortManifest, manifest_path: Path
    ) -> ShortRecord:
        video = paths.shorts_exports / manifest.filename
        artifacts: list[ShortArtifact] = []
        for kind, path in (
            ("video", video),
            ("manifest", manifest_path),
            ("log", video.with_name(f"{video.stem}.log")),
        ):
            if path.is_file():
                artifacts.append(
                    ShortArtifact(
                        kind=kind,
                        filename=path.name,
                        size_bytes=path.stat().st_size,
                        url=f"/api/projects/{slug}/shorts/exports/{path.name}",
                    )
                )

        return ShortRecord(
            short_id=manifest.short_id,
            project_slug=slug,
            filename=manifest.filename,
            url=f"/api/projects/{slug}/shorts/exports/{manifest.filename}",
            created_at=manifest.created_at,
            duration_seconds=manifest.duration_seconds,
            size_bytes=manifest.size_bytes or (video.stat().st_size if video.is_file() else 0),
            width=manifest.width,
            height=manifest.height,
            source_render_id=manifest.source_render_id,
            source_video=manifest.source_video,
            cache_key=manifest.cache_key,
            section_numbers=[s.number for s in manifest.plan.segments],
            section_titles=[s.title for s in manifest.plan.segments],
            job_id=manifest.job_id,
            artifacts=artifacts,
            caption_mode=manifest.caption_mode,
            caption_preset=manifest.caption_style.preset if manifest.caption_style else None,
        )

    def _describe(
        self,
        slug: str,
        paths: ProjectPaths,
        manifest: RenderManifest,
        render_id: str,
        manifest_path: Path,
    ) -> ShortSourceRender:
        video = paths.exports / manifest.source.filename
        exists = video.is_file()
        size = video.stat().st_size if exists else 0

        issue: str | None = None
        if not exists:
            issue = "Video dosyası artık diskte yok."
        elif size != manifest.source.size_bytes:
            issue = "Video dosyası oluşturulduğundan beri değişmiş."
        elif not manifest.source.has_audio:
            issue = "Bu videoda ses yok."

        created = manifest.written_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        return ShortSourceRender(
            render_id=render_id,
            project_slug=slug,
            filename=manifest.source.filename,
            url=f"/api/projects/{slug}/exports/{manifest.source.filename}",
            created_at=created,
            duration_seconds=manifest.total_duration_seconds,
            width=manifest.source.width,
            height=manifest.source.height,
            fps=manifest.profile.fps,
            quality=manifest.profile.quality,
            size_bytes=size or manifest.source.size_bytes,
            section_count=len(manifest.entries),
            has_audio=manifest.source.has_audio,
            thumbnail_url=f"/api/projects/{slug}/shorts/sources/{render_id}/poster",
            usable=issue is None,
            issue=issue,
            status=JobStatus.COMPLETED.value,
            captions=self.caption_support(paths, manifest),
        )

    def _render_id(self, manifest: RenderManifest, manifest_path: Path) -> str:
        """The job id where one was recorded, else the export's own stem.

        Older exports and hand-copied files still get a stable, unique id, and
        it is always drawn from a filename this app generated.
        """
        if manifest.render_job_id:
            return manifest.render_job_id
        return manifest_path.name[: -len(MANIFEST_SUFFIX)]

    def _render_job_statuses(self, slug: str) -> dict[str, JobStatus]:
        from app.render.jobs import get_job_manager

        try:
            jobs = get_job_manager().list_jobs(project_slug=slug, limit=200)
        except Exception:  # noqa: BLE001 - listing sources must not depend on the queue
            return {}
        return {job.id: job.status for job in jobs}


def _cut_names(manifest: ShortManifest) -> set[str]:
    """Segment-cache filenames this Short's plan would have produced."""
    if len(manifest.plan.groups) < 2:
        # A single-group Short is cut and composed in one pass, so it never
        # wrote a cached segment in the first place.
        return set()
    # Cuts are content-addressed by the file they came out of, which is the clean
    # master whenever the Short was not built from the captioned export.
    provenance = manifest.captions
    checksum = (
        provenance.clean_master_sha256
        if provenance is not None and provenance.clean_master_sha256
        else manifest.source_sha256
    )
    return {
        segment_cache_name(
            checksum,
            group.start_seconds,
            group.end_seconds,
            manifest.fps,
        )
        for group in manifest.plan.groups
    }


def _require_safe_id(value: str, kind: str) -> None:
    if not _SAFE_ID.match(value or ""):
        raise ValidationError(
            ErrorCode.PATH_TRAVERSAL,
            f"'{value}' geçerli bir kimlik değil.",
            details="kimlikler yalnızca harf, rakam, nokta, tire ve alt çizgi içerebilir",
            http_status=400,
        )


#: Re-exported so callers can build a manifest path without importing two modules.
__all__ = [
    "ShortsService",
    "manifest_path_for",
    "MIN_CLIP_SECONDS",
]
