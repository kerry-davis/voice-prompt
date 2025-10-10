from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from typing import Iterable

import numpy as np
from pywhispercpp.model import Model

logger = logging.getLogger(__name__)


class StreamingTranscriber:
    def __init__(
        self,
        model_size: str,
        sample_rate: int,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if device != "cpu":
            logger.warning("pywhispercpp only supports CPU decoding; forcing device=cpu")
        if compute_type not in {"int8", "q5_1", "q8_0"}:
            logger.debug("compute_type '%s' ignored by pywhispercpp backend", compute_type)
        self.sample_rate = sample_rate
        self._loop = loop or asyncio.get_event_loop()
        self._cpu_threads = max(1, min(8, (os.cpu_count() or 4)))
        self._lock = asyncio.Lock()
        self._model = Model(
            model_size,
            n_threads=self._cpu_threads,
            translate=False,
            single_segment=True,
            print_progress=False,
            print_realtime=False,
            language="en",
            no_context=True,
            suppress_blank=True,
        )

    async def transcribe(self, pcm: bytes, *, temperature: float = 0.0) -> str:
        if not pcm:
            return ""
        async with self._lock:
            return await self._loop.run_in_executor(
                None,
                partial(self._run_transcribe, pcm, temperature),
            )

    def _run_transcribe(self, pcm: bytes, temperature: float) -> str:
        audio = self._pcm_to_float32(pcm)
        if audio.size == 0:
            return ""
        segments = self._model.transcribe(
            audio,
            n_processors=None,
            temperature=temperature,
            language="en",
            suppress_blank=True,
            no_context=True,
            single_segment=True,
            max_len=0,
        )
        pieces: list[str] = []
        for segment in segments:
            text = getattr(segment, "text", "").strip()
            if text:
                pieces.append(text)
        transcript = " ".join(pieces).strip()
        logger.debug("ASR transcript: %s", transcript)
        return transcript

    def _pcm_to_float32(self, pcm: bytes) -> np.ndarray:
        if not pcm:
            return np.array([], dtype=np.float32)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return audio


def merge_transcripts(segments: Iterable[str]) -> str:
    return " ".join(s.strip() for s in segments if s).strip()
