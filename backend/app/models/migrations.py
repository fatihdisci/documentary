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

#: Maps "from version" -> function producing the next version's dict.
#: Empty at v1 because there is nothing older; the machinery is exercised by
#: tests using a synthetic v0 so it cannot rot before it is first needed.
MIGRATIONS: dict[int, Callable[[RawProject], RawProject]] = {}


def migrate(raw: RawProject) -> RawProject:
    """Upgrade a raw project dict to the current schema version in place-ish.

    Raises if the file comes from a *newer* app version, which we cannot
    meaningfully downgrade.
    """
    version = raw.get("schemaVersion", raw.get("schema_version", 1))
    if not isinstance(version, int):
        raise ValidationError(
            ErrorCode.SCHEMA_VALIDATION,
            "The project file's schemaVersion is not a number.",
            details=f"schemaVersion={version!r}",
        )

    if version > SCHEMA_VERSION:
        raise ValidationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"This project was created by a newer version of the app "
            f"(schema v{version}; this build understands up to v{SCHEMA_VERSION}).",
            details=f"file schemaVersion={version}, supported={SCHEMA_VERSION}",
        )

    working = dict(raw)
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValidationError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                f"No migration is registered from schema v{version} to v{version + 1}.",
                details=f"available migrations: {sorted(MIGRATIONS)}",
            )
        logger.info("migrating project schema v%d -> v%d", version, version + 1)
        working = step(working)
        version += 1
        working["schemaVersion"] = version

    working["schemaVersion"] = SCHEMA_VERSION
    return working
