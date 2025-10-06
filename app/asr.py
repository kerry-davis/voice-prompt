from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Iterable

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class StreamingTranscriber:
    def __init__(
        self,
        model_size: str,
        sample_rate: int,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.sample_rate = sample_rate
        self._loop = asyncio.get_event_loop()
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )

    async def transcribe(self, pcm: bytes, *, temperature: float = 0.0) -> str:
        if not pcm:
            return ""
        return await self._loop.run_in_executor(
            None,
            partial(self._run_transcribe, pcm, temperature),
        )

    def _run_transcribe(self, pcm: bytes, temperature: float) -> str:
        audio = self._pcm_to_float32(pcm)
        if audio.size == 0:
            return ""
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            temperature=temperature,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        pieces: list[str] = []
        for segment in segments:
            text = segment.text.strip()
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
