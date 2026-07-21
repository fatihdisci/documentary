"""Shared pytest fixtures.

Every test runs against a throwaway data directory so nothing touches the user's
real projects.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app at a temporary data directory for the whole test session."""
    data_dir = tmp_path_factory.mktemp("evb-data")
    monkeypatch.setenv("EVB_DATA_DIR", str(data_dir))

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    yield data_dir
    get_settings.cache_clear()


@pytest.fixture
def settings():  # noqa: ANN201 - fixture return type is the Settings object
    from app.config import get_settings

    return get_settings()


@pytest.fixture
def client(isolated_data_dir: Path):  # noqa: ANN201
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def have_ffmpeg() -> bool:
    from app.config import get_settings

    return get_settings().resolve_tool("ffmpeg") is not None


requires_ffmpeg = pytest.mark.skipif(
    not have_ffmpeg() and not os.environ.get("EVB_REQUIRE_FFMPEG"),
    reason="ffmpeg not available",
)
