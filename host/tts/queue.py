"""Serialized playback queue."""
from __future__ import annotations
import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

# DEBUG-TAG: tts-queue
# Grep: grep -E "tts-(queue|server)"

_STOP = object()  # sentinel distinct from any str value


class PlaybackQueue:
    def __init__(self, play_fn: Callable[[str], Awaitable[None]]):
        self._play = play_fn
        self._q: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._run())

    async def enqueue(self, text: str) -> None:
        log.debug("tts-queue: enqueue %r (size=%d)", text[:40], self._q.qsize())
        await self._q.put(text)

    async def drain(self) -> None:
        await self._q.join()

    async def stop(self) -> None:
        await self._q.put(_STOP)
        if self._worker:
            await self._worker

    async def _run(self) -> None:
        while True:
            item = await self._q.get()
            try:
                if item is _STOP:
                    return
                await self._play(item)
            except Exception:
                log.exception("tts-queue: play failed")
            finally:
                self._q.task_done()
