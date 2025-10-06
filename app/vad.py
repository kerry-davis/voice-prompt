from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterator

import webrtcvad


@dataclass(slots=True)
class VadResult:
    is_speech: bool
    reached_silence: bool


class SilenceTracker:
    def __init__(self, sample_rate: int, silence_ms: int) -> None:
        self.sample_rate = sample_rate
        self.vad = webrtcvad.Vad(2)
        self.frame_ms = 30
        self._frame_bytes = int(sample_rate * self.frame_ms / 1000) * 2
        self._pending = bytearray()
        self._speech_active = False
        self._silence_frames = 0
        self._silence_limit = max(1, silence_ms // self.frame_ms)

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def feed(self, data: bytes) -> Iterator[VadResult]:
        if not data:
            return iter(())
        self._pending.extend(data)
        results: deque[VadResult] = deque()
        while len(self._pending) >= self._frame_bytes:
            frame = bytes(self._pending[: self._frame_bytes])
            del self._pending[: self._frame_bytes]
            speech = self.vad.is_speech(frame, self.sample_rate)
            reached_silence = False
            if speech:
                self._speech_active = True
                self._silence_frames = 0
            else:
                if self._speech_active:
                    self._silence_frames += 1
                    if self._silence_frames >= self._silence_limit:
                        reached_silence = True
                        self._speech_active = False
                        self._silence_frames = 0
            results.append(VadResult(is_speech=speech, reached_silence=reached_silence))
        return iter(results)

    def reset(self) -> None:
        self._pending.clear()
        self._speech_active = False
        self._silence_frames = 0
