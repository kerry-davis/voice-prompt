from __future__ import annotations

import os
from dataclasses import dataclass


_MODEL_ALIASES = {
    "tiny-int8": "tiny",
    "tiny.en-int8": "tiny.en",
    "small-int8": "small",
    "small.en-int8": "small.en",
    "base-int8": "base",
    "base.en-int8": "base.en",
    "medium-int8": "medium",
    "medium.en-int8": "medium.en",
}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _resolve_model(name: str) -> str:
    return _MODEL_ALIASES.get(name, name)


@dataclass(slots=True)
class Settings:
    sample_rate: int = _int_env("STREAM_SAMPLE_RATE", 16000)
    partial_interval_ms: int = _int_env("STREAM_PARTIAL_INTERVAL_MS", 180)
    vad_silence_ms: int = _int_env("STREAM_VAD_SILENCE_MS", 350)
    partial_window_s: float = _float_env("STREAM_PARTIAL_WINDOW_S", 1.6)
    final_window_s: float = _float_env("STREAM_FINAL_WINDOW_S", 2.8)
    whisper_model: str = _resolve_model(os.getenv("STREAM_MODEL_WHISPER", "tiny.en-q5_1"))
    whisper_device: str = os.getenv("STREAM_MODEL_DEVICE", "cpu")
    whisper_compute_type: str = os.getenv("STREAM_MODEL_COMPUTE", "int8")
    tts_engine: str = os.getenv("STREAM_TTS_ENGINE", "pyttsx3")
    openai_model: str = os.getenv("STREAM_OPENAI_MODEL", "gpt-3.5-turbo")
    llm_temperature: float = _float_env("STREAM_LLM_TEMPERATURE", 0.7)


settings = Settings()
