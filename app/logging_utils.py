from __future__ import annotations

import asyncio
import logging


class SessionLogHandler(logging.Handler):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        super().__init__(level=logging.INFO)
        self._loop = loop
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - runtime integration
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._loop.call_soon_threadsafe(self._enqueue, message)

    def _enqueue(self, message: str) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            pass
