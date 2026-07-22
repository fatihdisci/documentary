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


#: Maps "from version" -> function producing the next version's dict.
MIGRATIONS: dict[int, Callable[[RawProject], RawProject]] = {
    1: _v1_to_v2,
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
