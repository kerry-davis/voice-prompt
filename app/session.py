from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any

from fastapi import WebSocket

from .asr import StreamingTranscriber
from .audio_buffer import AudioBuffer
from .config import settings
from .llm import ChatMessage, LLMConfig, LLMStreamer
from .logging_utils import SessionLogHandler
from .tts import PhraseAggregator, TTSPipeline
from .vad import SilenceTracker

logger = logging.getLogger(__name__)


class SessionState:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        transcriber: StreamingTranscriber,
        llm: LLMStreamer,
        tts: TTSPipeline,
    ) -> None:
        self.websocket = websocket
        self.transcriber = transcriber
        self.llm = llm
        self.tts = tts
        self.audio_buffer = AudioBuffer(settings.sample_rate)
        self.vad = SilenceTracker(settings.sample_rate, settings.vad_silence_ms)
        self.partial_interval = settings.partial_interval_ms / 1000
        self._last_partial_time = 0.0
        self._last_partial_text = ""
        self._partial_lock = asyncio.Lock()
        self._finalize_lock = asyncio.Lock()
        self._llm_task: asyncio.Task | None = None
        self._aggregator: PhraseAggregator | None = None
        self._metrics: dict[str, float] = {}
        self._transcript_seq = 0
        self._conversation: list[ChatMessage] = []
        self._loop = asyncio.get_event_loop()
        self._log_queue: asyncio.Queue[str] | None = None
        self._log_handler: SessionLogHandler | None = None
        self._log_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._metrics.clear()
        self._log_queue = asyncio.Queue(maxsize=200)
        self._log_handler = SessionLogHandler(self._loop, self._log_queue)
        formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        self._log_handler.setFormatter(formatter)
        logging.getLogger().addHandler(self._log_handler)
        self._log_task = asyncio.create_task(self._pump_logs())

    async def handle_message(self, message: Any) -> None:
        if isinstance(message, (bytes, bytearray)):
            await self._handle_audio(bytes(message))
        else:
            await self._handle_json(message)

    async def close(self) -> None:
        await self.cancel_reply()
        self._log_metrics()
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._log_task:
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
            self._log_task = None
        if self._log_queue:
            self._log_queue = None

    async def _handle_json(self, message: Any) -> None:
        if isinstance(message, str):
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON message: %s", message)
                return
        else:
            payload = message
        if not isinstance(payload, dict):
            return
        msg_type = payload.get("type")
        if msg_type == "start":
            await self._handle_start(payload)
        elif msg_type == "stop":
            await self._handle_stop()
        elif msg_type == "cancel":
            await self.cancel_reply()

    async def _handle_start(self, payload: dict[str, Any]) -> None:
        self._metrics["t0_user_audio_start"] = self._loop.time()
        sample_rate = payload.get("sample_rate")
        if sample_rate and sample_rate != settings.sample_rate:
            await self.websocket.send_json(
                {
                    "type": "info",
                    "message": f"Server expects {settings.sample_rate}Hz; received {sample_rate}Hz.",
                }
            )

    async def _handle_stop(self) -> None:
        await self._finalize_transcript()

    async def _handle_audio(self, data: bytes) -> None:
        if not data:
            return
        if "t0_user_audio_start" not in self._metrics:
            self._metrics["t0_user_audio_start"] = self._loop.time()
        self.audio_buffer.append(data)
        for result in self.vad.feed(data):
            if result.is_speech:
                await self._maybe_emit_partial()
            if result.reached_silence:
                await self._finalize_transcript()

    async def _maybe_emit_partial(self) -> None:
        now = self._loop.time()
        if now - self._last_partial_time < self.partial_interval:
            return
        if self._partial_lock.locked():
            return
        window = self.audio_buffer.get_window(6.0)
        if not window:
            return
        async with self._partial_lock:
            try:
                text = await self.transcriber.transcribe(window, temperature=0.0)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                logger.exception("Partial transcription failed", exc_info=exc)
                await self.websocket.send_json(
                    {"type": "error", "message": "Partial transcription failed"}
                )
                return
            if not text or text == self._last_partial_text:
                return
            self._last_partial_time = now
            self._last_partial_text = text
            if "t1_first_partial" not in self._metrics:
                self._metrics["t1_first_partial"] = now
            await self.websocket.send_json({"type": "partial_transcript", "text": text})

    async def _finalize_transcript(self) -> None:
        if self._finalize_lock.locked():
            return
        async with self._finalize_lock:
            window = self.audio_buffer.get_window(10.0)
            self.audio_buffer.clear()
            self.vad.reset()
            self._last_partial_text = ""
            if not window:
                return
            try:
                text = await self.transcriber.transcribe(window, temperature=0.0)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                logger.exception("Final transcription failed", exc_info=exc)
                await self.websocket.send_json(
                    {"type": "error", "message": "Final transcription failed"}
                )
                return
            if not text:
                return
            now = self._loop.time()
            self._metrics["t2_final_transcript"] = now
            self._transcript_seq += 1
            transcript_id = f"utt-{self._transcript_seq}"
            await self.websocket.send_json(
                {"type": "final_transcript", "text": text, "id": transcript_id}
            )
            self._conversation.append({"role": "user", "content": text})
            await self._start_llm_reply()

    async def _start_llm_reply(self) -> None:
        await self.cancel_reply()
        self._aggregator = PhraseAggregator()
        self._llm_task = asyncio.create_task(self._run_llm_reply(self._aggregator))

    async def _run_llm_reply(self, aggregator: PhraseAggregator) -> None:
        sink = TTSSessionSink(self)
        await aggregator.start(sink)
        assistant_tokens: list[str] = []
        completed = False
        try:
            async for token in self.llm.stream(self._conversation):
                now = self._loop.time()
                if "t3_first_llm_token" not in self._metrics:
                    self._metrics["t3_first_llm_token"] = now
                await self.websocket.send_json(
                    {"type": "llm_token", "text": token, "done": False}
                )
                assistant_tokens.append(token)
                await aggregator.add_token(token)
            completed = True
        except asyncio.CancelledError:
            logger.info("LLM reply cancelled")
            raise
        except Exception as exc:  # pragma: no cover - runtime guard
            logger.exception("LLM streaming failed", exc_info=exc)
            await self.websocket.send_json({"type": "error", "message": "LLM failed"})
        finally:
            await aggregator.flush()
            await self.websocket.send_json({"type": "tts_complete"})
            await self.websocket.send_json({"type": "llm_token", "done": True})
            if completed:
                reply_text = "".join(assistant_tokens).strip()
                if reply_text:
                    self._conversation.append({"role": "assistant", "content": reply_text})

    async def cancel_reply(self) -> None:
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()
            try:
                await self._llm_task
            except asyncio.CancelledError:
                pass
            await self.websocket.send_json({"type": "info", "message": "Reply cancelled"})
        self._llm_task = None
        self._aggregator = None

    def _log_metrics(self) -> None:
        if not self._metrics:
            return
        t0 = self._metrics.get("t0_user_audio_start")
        if not t0:
            return
        deltas = {}
        for key in ("t1_first_partial", "t2_final_transcript", "t3_first_llm_token", "t4_first_tts_audio"):
            timestamp = self._metrics.get(key)
            if timestamp:
                deltas[key] = round((timestamp - t0) * 1000, 2)
        if deltas:
            logger.info("Latency ms: %s", deltas)

    async def _pump_logs(self) -> None:
        if not self._log_queue:
            return
        try:
            while True:
                message = await self._log_queue.get()
                await self.websocket.send_json({"type": "log", "message": message})
        except asyncio.CancelledError:  # pragma: no cover - coordination path
            pass


class TTSSessionSink:
    def __init__(self, session: SessionState) -> None:
        self.session = session

    async def handle_phrase(self, seq: int, text: str) -> None:
        if not text:
            return
        idx = 0
        try:
            async for chunk in self.session.tts.synthesize(text):
                if not chunk:
                    continue
                now = self.session._loop.time()
                if "t4_first_tts_audio" not in self.session._metrics:
                    self.session._metrics["t4_first_tts_audio"] = now
                await self.session.websocket.send_json(
                    {
                        "type": "tts_chunk",
                        "seq": seq,
                        "index": idx,
                        "audio_b64": base64.b64encode(chunk).decode("ascii"),
                        "mime": "audio/wav",
                    }
                )
                idx += 1
        except Exception as exc:  # pragma: no cover - runtime guard
            logger.exception("TTS failed for seq %s", seq, exc_info=exc)
            await self.session.websocket.send_json(
                {"type": "error", "message": f"TTS failed for seq {seq}"}
            )
        finally:
            await self.session.websocket.send_json({"type": "tts_phrase_done", "seq": seq})
