"""The Shorts-ready clean master: the same film, without burned-in captions.

The problem this solves, stated plainly: **captions burned into an MP4 are
permanent.** A Short cut from a captioned 16:9 export and centred on a 1080x1920
canvas shrinks those captions to roughly a third of their designed size, and
nothing can be done about it after the fact. OCR, inpainting, cropping and blur
masks all destroy picture the user deliberately rendered.

So the clean master is produced *alongside* the normal export, from the same
timeline, and only ever as an extra:

* the normal export is untouched — same filename, same codec, same captions,
  same artifacts, same UI;
* the clean master has identical composition, Ken Burns motion, titles, scene
  text, watermark, scrim, fades, transitions, timing, codec profile, frame rate
  and audio mix, and differs in exactly one respect: no narration subtitles;
* it is cached in its own clip namespace, so building it can never evict or
  invalidate the captioned clips the normal export just used.

Two shortcuts are taken, both provably safe:

* if the render had no burned-in subtitles at all, the normal export *is* a
  clean master, and is recorded as one at zero cost;
* if a clean master with the same content key already exists, it is reused.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app.models.project import Project
from app.storage.layout import ProjectPaths
from app.timing.schedule import Timeline

logger = logging.getLogger("evb.render.clean_master")

#: Namespaces the clean master's scene-clip cache. ``render_scene_clip`` clears a
#: unit's superseded clips with a non-recursive glob, so this subdirectory keeps
#: the two sets of clips from ever deleting each other.
CLEAN_MASTER_CACHE_SLUG = "clean"

CLEAN_MASTER_SUFFIX = "-clean.mp4"


@dataclass(frozen=True)
class CleanMasterPlan:
    """What, if anything, the clean-master stage has to do."""

    #: False when the project opted out. Nothing is built and nothing is claimed.
    wanted: bool
    #: True when the normal export already has no burned-in captions, so it is
    #: the clean master and no second encode runs.
    reuse_primary_export: bool
    #: Why, in one line, for the render log.
    reason: str

    @property
    def needs_pass(self) -> bool:
        return self.wanted and not self.reuse_primary_export


def plan_clean_master(project: Project, timeline: Timeline) -> CleanMasterPlan:
    """Decide whether a second pass is needed, before any work is done."""
    if not project.export.prepare_clean_master_for_shorts:
        return CleanMasterPlan(
            wanted=False,
            reuse_primary_export=False,
            reason=(
                "'Prepare a clean master for Shorts' is off, so no clean master was made. "
                "Shorts from this render use the captions already in the picture."
            ),
        )

    burned = bool(project.subtitles.burn_in and timeline.cues)
    if not burned:
        return CleanMasterPlan(
            wanted=True,
            reuse_primary_export=True,
            reason=(
                "this export has no burned-in subtitles, so it is its own clean master; "
                "no second pass was needed"
            ),
        )
    return CleanMasterPlan(
        wanted=True,
        reuse_primary_export=False,
        reason="subtitles are burned into the export, so a subtitle-free pass was rendered",
    )


def clean_master_project(project: Project) -> Project:
    """A copy of the project with subtitle burn-in off, and nothing else changed.

    A deep copy on purpose: the render in flight keeps using the real project
    object, and nothing this branch does can reach it. ``export_srt`` and
    ``export_scene_srt`` are left alone because this branch never writes
    artifacts — the user's ``.srt`` files come from the normal export only.
    """
    clone = project.model_copy(deep=True)
    clone.subtitles.burn_in = False
    return clone


def clean_master_path(paths: ProjectPaths, export: Path) -> Path:
    """Where the clean master for ``export`` lives.

    Named after the export it twins, in the package directory rather than beside
    the export, so it never shows up in the user's export list. The manifest
    still records the filename explicitly — nothing downstream reconstructs this.
    """
    return paths.shorts_source / f"{export.stem}{CLEAN_MASTER_SUFFIX}"


def clean_master_cache_key(project: Project, timeline: Timeline, profile_fps: int) -> str:
    """Content key for a clean master, for the render log and for debugging.

    Not a cache *lookup* key: the clean master is bound to one export by name, so
    a stale one is overwritten rather than matched. Recorded so two renders that
    should have produced the same master can be compared when one does not.
    """
    payload = "\x1f".join(
        [
            project.project_id,
            f"{timeline.total_duration_seconds:.4f}",
            str(profile_fps),
            project.model_dump_json(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
