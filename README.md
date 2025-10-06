# Voice Prompt Studio

Modern real-time voice experience that streams ASR partials, LLM tokens, and TTS audio over WebSockets with a monitoring-friendly UI.

## Features
- **Low-latency pipeline** using FastAPI, faster-whisper (tiny.en default), streaming LLM, phrase-based TTS, and per-session latency metrics.
- **Modern web client** with live capture status, conversation timeline, manual playback queue, real-time server log pane, and light/dark themes.
- **WebSocket protocol** for binary PCM upload (16 kHz mono) plus JSON control and rich server-to-client events (`partial_transcript`, `llm_token`, `tts_chunk`, etc.).
- **Resilience** via VAD-based end-of-speech detection, cancellation handling, and graceful error messages surfaced directly in the UI.

## Getting Started
1. Create/activate the local venv and install dependencies:
   ```bash
   ./run.sh  # first launch installs requirements unless SKIP_INSTALL=1
   ```
2. Open `http://localhost:8000` in a Chromium or Firefox browser, allow microphone access, and use the control panel to start/stop streaming.

### Environment Tweaks
- `STREAM_MODEL_WHISPER` (e.g. `base.en`) and `STREAM_MODEL_DEVICE`/`STREAM_MODEL_COMPUTE` control ASR size and target (CPU/GPU).
- `OPENAI_API_KEY` enables real LLM streaming; otherwise a synthetic token generator echoes responses.

### UI Highlights
- Control panel exposes latency chip and theme toggle.
- Live capture card shows partial/assistant streams while the transcript card keeps full results scrollable.
- Playback card queues TTS chunks for manual listening.
- Logs card mirrors recent backend log lines for quick diagnosis.

## Testing
Run unit tests (currently covering phrase aggregation behaviour) with:
```bash
.venv/bin/pytest
```
