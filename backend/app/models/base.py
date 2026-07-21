"""Shared Pydantic base classes.

Every model that crosses the wire uses camelCase aliases so the TypeScript side
never has to translate field names.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    """API model: snake_case in Python, camelCase on the wire, both accepted."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )
