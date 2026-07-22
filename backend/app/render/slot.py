"""The single CPU-heavy render slot.

FFmpeg saturates the CPU on its own, so exactly one heavy encode runs at a time
across the whole process. The long-render queue already serialized itself; this
extends the same guarantee across job *types*, so starting a Short never ends up
competing with a long render for the same cores.

The semaphore is bound lazily to whichever event loop is running, for the same
reason the job queues are: building it at import time pins it to a loop that a
uvicorn reload or the next test will have replaced.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger("evb.slot")

_semaphore: asyncio.Semaphore | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _bound_semaphore() -> asyncio.Semaphore:
    global _semaphore, _loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _loop is not loop:
        _semaphore = asyncio.Semaphore(1)
        _loop = loop
    return _semaphore


@contextlib.asynccontextmanager
async def render_slot(*, label: str = "render") -> AsyncIterator[None]:
    """Hold the process-wide render slot for the duration of the block."""
    semaphore = _bound_semaphore()
    if semaphore.locked():
        logger.info("%s is waiting for the render slot", label)
    async with semaphore:
        yield


def reset_render_slot() -> None:
    """Drop the binding. Used by tests to isolate state between event loops."""
    global _semaphore, _loop
    _semaphore = None
    _loop = None
