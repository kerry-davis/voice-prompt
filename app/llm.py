from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator, Iterable

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - optional dep import guard
    AsyncOpenAI = None  # type: ignore

logger = logging.getLogger(__name__)


ChatMessage = dict[str, str]


@dataclass(slots=True)
class LLMConfig:
    model: str
    temperature: float


class LLMStreamer:
    def __init__(self, config: LLMConfig) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        self.config = config
        if api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(api_key=api_key)
        else:
            if not api_key:
                logger.warning("OPENAI_API_KEY not set. Falling back to synthetic tokens.")
            elif AsyncOpenAI is None:
                logger.warning("openai package unavailable. Falling back to synthetic tokens.")
            self._client = None

    async def stream(self, history: Iterable[ChatMessage]) -> AsyncIterator[str]:
        if self._client:
            async for token in self._stream_openai(history):
                yield token
        else:
            async for token in self._stream_fallback(history):
                yield token

    async def _stream_openai(self, history: Iterable[ChatMessage]) -> AsyncIterator[str]:
        messages = list(history)
        stream = await self._client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            stream=True,
        )
        async for event in stream:
            try:
                delta = event.choices[0].delta
            except (AttributeError, IndexError):  # pragma: no cover - defensive
                continue
            text = getattr(delta, "content", None)
            if text:
                yield text

    async def _stream_fallback(self, history: Iterable[ChatMessage]) -> AsyncIterator[str]:
        *_, last = list(history)
        content = last.get("content", "") if isinstance(last, dict) else ""
        tokens = content.split()
        if not tokens:
            tokens = ["I'm", "thinking", "..."]
        for token in tokens:
            await asyncio.sleep(0.02)
            yield token + " "
