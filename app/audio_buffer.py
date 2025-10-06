from __future__ import annotations

from collections import deque


class AudioBuffer:
    """Ring buffer for mono 16-bit PCM audio."""

    def __init__(self, sample_rate: int, max_seconds: float = 30.0) -> None:
        self.sample_rate = sample_rate
        self._bytes_per_sample = 2
        self._max_samples = int(sample_rate * max_seconds)
        self._buffer = deque[bytes]()
        self._total_samples = 0

    @property
    def total_samples(self) -> int:
        return self._total_samples

    def clear(self) -> None:
        self._buffer.clear()
        self._total_samples = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        samples = len(chunk) // self._bytes_per_sample
        self._buffer.append(chunk)
        self._total_samples += samples
        self._trim_if_needed()

    def get_window(self, seconds: float) -> bytes:
        if seconds <= 0:
            return b""
        samples_needed = min(self._total_samples, int(seconds * self.sample_rate))
        if samples_needed <= 0:
            return b""
        bytes_needed = samples_needed * self._bytes_per_sample
        collected: list[bytes] = []
        total = 0
        for chunk in reversed(self._buffer):
            if total >= bytes_needed:
                break
            if total + len(chunk) <= bytes_needed:
                collected.append(chunk)
                total += len(chunk)
            else:
                tail = chunk[-(bytes_needed - total) :]
                collected.append(tail)
                total = bytes_needed
                break
        collected.reverse()
        return b"".join(collected)

    def _trim_if_needed(self) -> None:
        max_samples = self._max_samples
        if self._total_samples <= max_samples:
            return
        target_samples = max_samples
        removed = 0
        while self._buffer and self._total_samples - removed > target_samples:
            chunk = self._buffer[0]
            chunk_samples = len(chunk) // self._bytes_per_sample
            if self._total_samples - removed - chunk_samples < target_samples:
                excess_samples = self._total_samples - removed - target_samples
                excess_bytes = excess_samples * self._bytes_per_sample
                self._buffer[0] = chunk[excess_bytes:]
                removed += excess_samples
                break
            removed += chunk_samples
            self._buffer.popleft()
        self._total_samples = max(self._total_samples - removed, 0)
