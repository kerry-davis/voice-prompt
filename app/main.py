from __future__ import annotations

import asyncio
import logging
import base64
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from .asr import StreamingTranscriber
from .config import settings
from .llm import LLMConfig, LLMStreamer
from .session import SessionState


logging.basicConfig(level=logging.INFO)

app = FastAPI()


@app.on_event("startup")
async def _startup() -> None:
    loop = asyncio.get_running_loop()
    app.state.transcriber = StreamingTranscriber(
        settings.whisper_model,
        settings.sample_rate,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        loop=loop,
    )
    app.state.llm = LLMStreamer(LLMConfig(model=settings.openai_model, temperature=settings.llm_temperature))


@app.get("/")
async def index() -> HTMLResponse:
    client_dir = Path(__file__).resolve().parent.parent / "client"
    index_file = client_dir / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Voice Prompt Streaming</h1>")
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = SessionState(
        websocket,
        transcriber=app.state.transcriber,
        llm=app.state.llm,
    )
    await session.start()
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                await session.handle_message(data)
                continue
            text = message.get("text")
            if text is not None:
                await session.handle_message(text)
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()


static_dir = Path(__file__).resolve().parent.parent / "client"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


class VoiceRequest(BaseModel):
    audio_b64: str
    sample_rate: int = settings.sample_rate


@app.post("/api/voice")
async def legacy_voice(request: VoiceRequest) -> dict[str, str]:
    if request.sample_rate != settings.sample_rate:
        raise HTTPException(status_code=400, detail="Unsupported sample rate")
    try:
        pcm = base64.b64decode(request.audio_b64)
    except Exception as exc:  # pragma: no cover - parse guard
        raise HTTPException(status_code=400, detail="Invalid audio encoding") from exc

    transcript = await app.state.transcriber.transcribe(pcm)
    if not transcript:
        raise HTTPException(status_code=422, detail="Unable to transcribe")

    conversation = [{"role": "user", "content": transcript}]
    tokens: list[str] = []
    async for token in app.state.llm.stream(conversation):
        tokens.append(token)
    assistant_reply = "".join(tokens).strip()

    return {
        "transcript": transcript,
        "assistant": assistant_reply,
    }


def get_app() -> FastAPI:
    return app
