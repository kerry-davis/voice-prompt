import asyncio

import pytest

from app.tts import PhraseAggregator


class DummySink:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def handle_phrase(self, seq: int, text: str) -> None:
        await asyncio.sleep(0)
        self.calls.append((seq, text))


@pytest.mark.asyncio
async def test_phrase_emission_and_flush():
    sink = DummySink()
    aggregator = PhraseAggregator()
    await aggregator.start(sink)
    await aggregator.add_token("Hello ")
    await aggregator.add_token("world.")
    await aggregator.flush()
    assert sink.calls == [(1, "Hello world.")]
