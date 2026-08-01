"""Schema-version migration chain for project.json.

Adding a new schema version means: bump ``SCHEMA_VERSION`` in models/project.py,
append a function to ``MIGRATIONS`` keyed by the version it upgrades *from*, and
add a fixture-based test. Old projects then open transparently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.errors import ErrorCode, ValidationError
from app.models.project import SCHEMA_VERSION

logger = logging.getLogger("evb.migrations")

RawProject = dict[str, object]

def _v1_to_v2(raw: RawProject) -> RawProject:
    """v2 added ``export.prepareCleanMasterForShorts``, defaulting to on.

    A clean master is a second subtitle-free encode of the whole video, which
    roughly doubles render time whenever subtitles are burned in. That is a fine
    default for a project created after the feature existed — the user is told
    what it costs before they render — but it must never be applied silently to
    a project someone made earlier. So the migration writes the flag explicitly
    off; the user turns it on when they actually want Shorts captions.
    """
    working = dict(raw)
    export = working.get("export")
    export = dict(export) if isinstance(export, dict) else {}
    export.setdefault("prepareCleanMasterForShorts", False)
    working["export"] = export
    return working


def _v2_to_v3(raw: RawProject) -> RawProject:
    """v3 added the branded opening and a hook on every planned Short.

    Both are additive and both have working defaults, so the migration only has
    to make the blocks exist. Two decisions are worth stating:

    * ``longIntro`` is written **enabled**, with its titles left blank. Blank
      titles resolve to the project's own animal names at render time, so an
      untouched old project gains the same correct opening every new project
      gets — which is the whole point of a channel identity — and one toggle in
      Texts turns it off again. The current renderer prepends it as a separate
      pre-roll before the reusable content timeline.
    * Every planned Short gains an empty ``hook``. Empty means "draw nothing", so
      a Short re-cut from an old plan is byte-for-byte the Short it was before.

    The one thing that does change for an old project is the Shorts clean
    master: an intro must never reach the file Shorts are cut from, so a project
    that renders without burned-in subtitles can no longer publish its export as
    its own clean master and pays a second pass instead. Turning the intro off
    restores the shortcut, and the render log says so in as many words.
    """
    working = dict(raw)
    long_intro = working.get("longIntro")
    working["longIntro"] = dict(long_intro) if isinstance(long_intro, dict) else {"enabled": True}

    plan = working.get("shortsPlan")
    if isinstance(plan, dict):
        plan = dict(plan)
        shorts = plan.get("shorts")
        if isinstance(shorts, list):
            plan["shorts"] = [
                {**item, "hook": item.get("hook") or {"lines": []}}
                if isinstance(item, dict)
                else item
                for item in shorts
            ]
        working["shortsPlan"] = plan
    return working


def _v3_to_v4(raw: RawProject) -> RawProject:
    """Retimes the first version of the opening and makes hooks two-beat reveals.

    Only exact v3 house defaults are changed. Any timing the user edited is
    authored work and is preserved.
    """
    working = dict(raw)
    long_intro = working.get("longIntro")
    if isinstance(long_intro, dict):
        long_intro = dict(long_intro)
        if (
            long_intro.get("duration") == 2.6
            and long_intro.get("typewriterDuration") == 1.3
            and long_intro.get("stampAt") == 1.7
        ):
            long_intro.update(
                {
                    "duration": 4.2,
                    "typewriterDuration": 1.8,
                    "stampAt": 2.65,
                    "fadeOutSeconds": 0.65,
                }
            )
        working["longIntro"] = long_intro

    plan = working.get("shortsPlan")
    if isinstance(plan, dict):
        plan = dict(plan)
        shorts = plan.get("shorts")
        if isinstance(shorts, list):
            migrated_shorts: list[object] = []
            for item in shorts:
                if not isinstance(item, dict):
                    migrated_shorts.append(item)
                    continue
                item = dict(item)
                hook = item.get("hook")
                if isinstance(hook, dict):
                    hook = dict(hook)
                    if hook.get("durationSeconds") == 1.4:
                        hook["durationSeconds"] = 2.2
                    item["hook"] = hook
                migrated_shorts.append(item)
            plan["shorts"] = migrated_shorts
        working["shortsPlan"] = plan
    return working


def _v4_to_v5(raw: RawProject) -> RawProject:
    """v5 removed the Instagram and Facebook copy from every planned Short.

    The app publishes to YouTube and TikTok only, so those two blocks were text
    the author had to write with nowhere for it to go. Dropping them is purely
    subtractive: the sections, hook, YouTube metadata and TikTok copy that decide
    what a Short *is* are untouched, so every Short re-cuts exactly as before.
    """
    working = dict(raw)
    plan = working.get("shortsPlan")
    if isinstance(plan, dict):
        plan = dict(plan)
        shorts = plan.get("shorts")
        if isinstance(shorts, list):
            plan["shorts"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"instagram", "facebook"}
                }
                if isinstance(item, dict)
                else item
                for item in shorts
            ]
        working["shortsPlan"] = plan
    return working


#: Maps "from version" -> function producing the next version's dict.
MIGRATIONS: dict[int, Callable[[RawProject], RawProject]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
    4: _v4_to_v5,
}


def migrate(raw: RawProject) -> RawProject:
    """Upgrade a raw project dict to the current schema version in place-ish.

    Raises if the file comes from a *newer* app version, which we cannot
    meaningfully downgrade.
    """
    version = raw.get("schemaVersion", raw.get("schema_version", 1))
    if not isinstance(version, int):
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            "Proje dosyasının sürüm bilgisi sayı değil.",
            details=f"schemaVersion={version!r}",
        )

    if version > SCHEMA_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"Bu proje uygulamanın daha yeni bir sürümüyle oluşturulmuş "
            f"(sürüm {version}; bu kurulum en fazla {SCHEMA_VERSION} sürümünü anlıyor).",
            details=f"file schemaVersion={version}, supported={SCHEMA_VERSION}",
        )

    working = dict(raw)
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValidationError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                f"{version} sürümünden {version + 1} sürümüne geçiş tanımlı değil.",
                details=f"available migrations: {sorted(MIGRATIONS)}",
            )
        logger.info("migrating project schema v%d -> v%d", version, version + 1)
        working = step(working)
        version += 1
        working["schemaVersion"] = version

    working["schemaVersion"] = SCHEMA_VERSION
    return working
