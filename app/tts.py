from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import AsyncIterator

try:
    import pyttsx3
except Exception as exc:  # pragma: no cover - optional dependency guard
    pyttsx3 = None
    logging.getLogger(__name__).warning("pyttsx3 unavailable: %s", exc)


logger = logging.getLogger(__name__)


_CHUNK_SIZE = 32 * 1024


class TTSPipeline:
    def __init__(self, *, max_workers: int = 2, rate: int = 190) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._loop = asyncio.get_event_loop()
        self._rate = rate
        self._thread_local = threading.local()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        audio = await self._loop.run_in_executor(self._executor, self._synthesize_blocking, text)
        for idx in range(0, len(audio), _CHUNK_SIZE):
            yield audio[idx : idx + _CHUNK_SIZE]

    def _synthesize_blocking(self, text: str) -> bytes:
        if not text.strip():
            return b""
        if pyttsx3 is None:
            logger.warning("pyttsx3 missing, using silent audio stub")
            return b""
        engine = self._get_engine()
        if engine is None:
            return b""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = tmp.name
        try:
            engine.save_to_file(text, path)
            engine.runAndWait()
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                logger.debug("Temporary TTS file cleanup failed", exc_info=True)

    def _get_engine(self):
        if pyttsx3 is None:
            return None
        engine = getattr(self._thread_local, "engine", None)
        if engine is not None:
            return engine
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            self._thread_local.engine = engine
            return engine
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to initialise pyttsx3 engine: %s", exc)
            return None


@dataclass
class Phrase:
    seq: int
    text: str


class PhraseAggregator:
    def __init__(self, *, punctuation: str = ".?!") -> None:
        self._punctuation = punctuation
        self._current: list[str] = []
        self._seq = 0
        self._output_queue: asyncio.Queue[Phrase] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None

    async def start(self, sink) -> None:
        if self._consumer_task:
            return
        self._consumer_task = asyncio.create_task(self._consume(sink))

    async def add_token(self, token: str) -> None:
        if not token:
            return
        self._current.append(token)
        text = "".join(self._current).strip()
        if not text:
            return
        if text[-1] in self._punctuation or len(text) >= 60:
            await self._emit_phrase(text)

    async def flush(self) -> None:
        if not self._consumer_task:
            return
        text = "".join(self._current).strip()
        self._current.clear()
        if text:
            await self._emit_phrase(text)
        await self._output_queue.put(Phrase(-1, ""))
        if self._consumer_task:
            await self._consumer_task
            self._consumer_task = None

    async def _emit_phrase(self, text: str) -> None:
        self._seq += 1
        await self._output_queue.put(Phrase(self._seq, text))
        self._current.clear()

    async def _consume(self, sink) -> None:
        while True:
            phrase = await self._output_queue.get()
            if phrase.seq == -1:
                break
            await sink.handle_phrase(phrase.seq, phrase.text)
