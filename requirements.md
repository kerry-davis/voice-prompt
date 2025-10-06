# Prompt Voice Streaming Upgrade Requirements

## 1. Purpose
Add real-time (low-latency) interaction: partial transcription, streaming LLM tokens, and incremental audio playback to reduce perceived round‑trip delay.

## 2. Scope
In-scope (phased):
1. WebSocket channel
2. Incremental ASR partial + final transcripts
3. Streaming LLM tokens
4. Incremental phrase-based TTS audio chunks
5. Basic latency metrics

Out-of-scope (now):
- True continuous diarization
- Multi-user auth
- Advanced punctuation restoration beyond model output
- GPU batching

## 3. Non-Goals
- Perfect real-time (<300ms) like native apps
- Cross-browser legacy support (focus: Chromium + recent Firefox)

## 4. Current Baseline (Phase 0)
POST /api/voice with full recorded blob → Whisper full decode → single LLM call → full TTS → return JSON + base64 WAV.

## 5. Phased Delivery

### Phase 1 (ASR partials) 30–45 min
- Add /ws WebSocket (FastAPI).
- Client: switch from MediaRecorder to AudioWorklet (raw 16-bit PCM mono 16 kHz).
- Send binary frames (~320 ms = 5120 samples @16kHz) labeled implicitly by order.
- Server: buffer PCM, run incremental transcription on rolling window (every ~1s) using faster-whisper (replace original whisper for speed).
- Emit:
  {type:"partial_transcript", text:"..."} (non-stable)
  {type:"final_transcript", text:"..."} (on silence / end-of-speech)
- Silence detection: WebRTC VAD (webrtcvad) with short frames (30 ms). After N ms silence (e.g. 600 ms) finalize.

### Phase 2 (LLM token streaming) +15–25 min
- When final_transcript emitted, start LLM stream (OpenAI streaming or local fallback that yields word-by-word).
- Forward tokens:
  {type:"llm_token", text:"token_fragment", done: false}
  Final message: {type:"llm_token", done:true}
- Maintain conversation memory only after done:true.

### Phase 3 (Incremental TTS) +30–45 min
- Phrase aggregator: accumulate tokens until punctuation (.?!, length > ~60 chars, or short pause).
- For each phrase, enqueue TTS synthesis (thread pool).
- Stream audio chunks:
  {type:"tts_chunk", seq:n, audio_b64:"...", mime:"audio/wav" }
  Completion per phrase:
  {type:"tts_phrase_done", seq:n}
  Conversation reply completion:
  {type:"tts_complete"}
- Client: play queued chunks via MediaSource / simple Blob append (can start with incremental <audio> blob creation per phrase).

### Phase 4 (Refinements)
- Basic latency logging (client-side: first partial, final transcript, first token, first audio).
- Fallback to non-streaming if WS fails.
- Optional: cancellation (user presses stop → send {type:"cancel"}).

## 6. Functional Requirements (FR)
FR1: WebSocket endpoint /ws accepts binary (PCM) + JSON control.
FR2: Must deliver first partial transcript < 1.5s after speech start (on typical CPU).
FR3: Must detect end-of-speech (600–800 ms silence) and emit final transcript.
FR4: Must stream LLM tokens within 300 ms after final transcript (if remote API responsive).
FR5: Must start TTS playback before full reply complete.
FR6: Must handle user early stop (mouse up) gracefully.
FR7: Memory only updated after full assistant reply.
FR8: On error, emit {type:"error", message:"..."} and allow session continuation.

## 7. Non-Functional Requirements
- Latency targets (desktop CPU):
  - Partial transcript cadence: ~1s
  - Time to first token: <2.5s post speech end
  - Time to first audio: <3.0s post speech end
- CPU: Single user acceptable on 4-core laptop (faster-whisper small / medium-int8).
- Resilience: If ASR fails once, notify and keep WS open.

## 8. Technology Choices
- ASR: faster-whisper (pip: faster-whisper) with int8 model (small / base).
- VAD: webrtcvad.
- LLM streaming: OpenAI (stream=True) or synthetic generator fallback (yields word-by-word).
- TTS: Keep pyttsx3 initially (phrase blocking) OR switch to Piper for faster synthesis (optional toggle). Start with pyttsx3; phrase-level concurrency via ThreadPoolExecutor(max_workers=2).

## 9. Message Types (Server → Client)
- partial_transcript: {type, text}
- final_transcript: {type, text, id}
- llm_token: {type, text?, done?}
- tts_chunk: {type, seq, audio_b64, mime}
- tts_phrase_done: {type, seq}
- tts_complete: {type}
- error: {type, message}
- info: {type, message}

Client → Server
- start: {type:"start", sample_rate:16000}
- stop: {type:"stop"} (user ended utterance)
- cancel: {type:"cancel"} (cancel current reply)
- (Audio frames): raw PCM Int16 LE

## 10. Client Implementation Notes
- Use AudioWorkletProcessor to capture 128/256 frame callbacks → resample to 16k (if context rate != 16000) → pack Int16 → send.
- Buffering: send frames immediately; no large aggregation (>40KB).
- Playback queue: Maintain Map(seq→AudioBufferSource). Start in order; if late, queue until previous done.

## 11. Server Flow (High-Level)
1. On WS accept: set session state (buffers, vad_state).
2. For each binary frame:
   - Append to PCM ring buffer.
   - Run VAD per 30 ms frame; track speech vs silence.
   - Every 1s or speech segment update: run incremental decode (faster-whisper transcribe on tail window or use segment timestamps) → emit partial.
3. On silence timeout OR explicit stop: finalize → emit final_transcript.
4. Spawn LLM streaming coroutine → forward tokens.
5. Phrase aggregator sends phrases to TTS queue; each finished phrase chunked & emitted.
6. On llm stream end & all phrases emitted: tts_complete.

## 12. Error Handling
- If ASR raises exception: emit error + advise retry.
- If LLM streaming fails mid-way: emit error, allow next utterance (do not store partial reply).
- If TTS phrase fails: skip phrase, emit info, continue next.

## 13. Testing Plan
Phase 1:
- Manual: speak short sentence; confirm partials appear before final.
- Latency logging (console.time).
Phase 2:
- Verify tokens appear incrementally (simulate slow network by delay injection).
Phase 3:
- Ensure audio begins before full text done; measure start time delta.
Automated (later):
- Mock LLM stream; feed deterministic tokens; assert ordering.

## 14. Metrics (console only initially)
Client timestamps:
- t0_user_audio_start
- t1_first_partial
- t2_final_transcript
- t3_first_llm_token
- t4_first_tts_audio
Print deltas.

## 15. Configuration
Environment variables:
- STREAM_MODEL_WHISPER=small-int8
- STREAM_VAD_SILENCE_MS=700
- STREAM_PARTIAL_INTERVAL_MS=1000
- STREAM_TTS_ENGINE=pyttsx3 (future: piper)

## 16. Risks & Mitigations
- Whisper model too slow: switch to smaller or quantized faster-whisper.
- pyttsx3 blocking: isolate in thread pool; per phrase synthesis.
- Audio drift (resample issues): verify sample count increments; fallback to offline resampler library if needed.

## 17. Incremental Commit Strategy
- Commit 1: dependencies + faster-whisper + ws skeleton.
- Commit 2: client AudioWorklet + binary streaming + partial transcripts.
- Commit 3: LLM token streaming.
- Commit 4: Phrase TTS streaming.
- Commit 5: Metrics + cleanup.

## 18. Estimated Time (focused)
- Phase 1: ~45 min initial
- Phase 2: ~25 min
- Phase 3: ~45–60 min (TTS chunking biggest variable)
Can stop after Phase 2 for usable improvement.

## 19. Future Enhancements
- Adaptive chunk size based on real-time factor (RTF).
- Overlap-and-merge decoding to reduce partial jitter.
- Local LLM via ollama with streaming.
- Opus audio transport (bandwidth savings).

## 20. Acceptance Criteria (Minimal Streaming Release)
- User sees at least one partial before finishing speech.
- Final transcript correctness comparable to baseline non-streaming.
- Tokens appear incrementally (≥5 token events for medium reply).
- Audio playback starts before full text done.
- No crashes on consecutive 3+ interactions.
