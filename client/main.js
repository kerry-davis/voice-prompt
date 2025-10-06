const SAMPLE_RATE = 16000;
const CHUNK_MS = 320;

const recordBtn = document.getElementById("recordBtn");
const stopBtn = document.getElementById("stopBtn");
const cancelBtn = document.getElementById("cancelBtn");
const partialPanel = document.getElementById("partial");
const finalPanel = document.getElementById("final");
const tokensPanel = document.getElementById("tokens");
const metricsPanel = document.getElementById("metrics");
const player = document.getElementById("player");

let ws;
let audioContext;
let workletNode;
let mediaStream;
let recording = false;
let metrics = {};

const phraseBuffers = new Map();
const playbackQueue = [];
let nextSeqToPlay = 1;
let playing = false;
let currentAudioUrl = null;

recordBtn.addEventListener("click", () => startRecording());
stopBtn.addEventListener("click", () => stopRecording());
cancelBtn.addEventListener("click", () => sendCancel());

async function startRecording() {
  if (recording) return;
  await ensureWebSocket();
  await setupAudio();
  recording = true;
  recordBtn.disabled = true;
  stopBtn.disabled = false;
  cancelBtn.disabled = false;
  resetPlayback();
  partialPanel.textContent = "";
  finalPanel.textContent = "";
  tokensPanel.textContent = "";
  metricsPanel.textContent = "";
  metrics = { t0_user_audio_start: performance.now() };
  ws.send(JSON.stringify({ type: "start", sample_rate: SAMPLE_RATE }));
}

async function stopRecording() {
  if (!recording) return;
  recording = false;
  teardownAudio();
  recordBtn.disabled = false;
  stopBtn.disabled = true;
  cancelBtn.disabled = true;
  ws.send(JSON.stringify({ type: "stop" }));
}

async function sendCancel() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "cancel" }));
  resetPlayback();
  cancelBtn.disabled = true;
}

function teardownAudio() {
  if (workletNode) {
    workletNode.port.onmessage = null;
    workletNode.disconnect();
    workletNode = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
}

async function ensureWebSocket() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => console.log("WebSocket open");
  ws.onclose = () => {
    console.log("WebSocket closed");
    recording = false;
    teardownAudio();
    resetPlayback();
    recordBtn.disabled = false;
    stopBtn.disabled = true;
    cancelBtn.disabled = true;
  };
  ws.onerror = (err) => console.error("WebSocket error", err);
  ws.onmessage = (event) => handleServerMessage(event);
  await new Promise((resolve) => {
    ws.addEventListener("open", resolve, { once: true });
  });
}

async function setupAudio() {
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioContext = new AudioContext({ sampleRate: 48000 });
  await audioContext.audioWorklet.addModule("/static/pcm-worklet.js");
  await audioContext.resume();
  workletNode = new AudioWorkletNode(audioContext, "pcm-processor", {
    processorOptions: { targetSampleRate: SAMPLE_RATE, chunkMs: CHUNK_MS },
  });
  const source = audioContext.createMediaStreamSource(mediaStream);
  source.connect(workletNode);
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  workletNode.connect(silentGain).connect(audioContext.destination);
  workletNode.port.onmessage = ({ data }) => {
    if (!recording || !data) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(data);
    }
  };
}

function handleServerMessage(event) {
  const data = typeof event.data === "string" ? JSON.parse(event.data) : null;
  if (!data) return;
  switch (data.type) {
    case "partial_transcript":
      partialPanel.textContent = data.text;
      if (!metrics.t1_first_partial) metrics.t1_first_partial = performance.now();
      break;
    case "final_transcript":
      finalPanel.textContent = data.text;
      if (!metrics.t2_final_transcript) metrics.t2_final_transcript = performance.now();
      break;
    case "llm_token":
      if (!data.done) {
        tokensPanel.textContent += data.text;
        if (!metrics.t3_first_llm_token) metrics.t3_first_llm_token = performance.now();
      }
      break;
    case "tts_chunk":
      handleTtsChunk(data);
      break;
    case "tts_phrase_done":
      finalizePhrase(data.seq);
      break;
    case "tts_complete":
      if (!recording) {
        tryPlayAudio();
      }
      break;
    case "info":
      console.log("info", data.message);
      break;
    case "error":
      console.error("error", data.message);
      break;
  }
  updateMetrics();
}

function handleTtsChunk({ seq, audio_b64 }) {
  const bytes = base64ToBytes(audio_b64);
  let entry = phraseBuffers.get(seq);
  if (!entry) {
    entry = { chunks: [], done: false };
    phraseBuffers.set(seq, entry);
  }
  entry.chunks.push(bytes);
  if (!metrics.t4_first_tts_audio) metrics.t4_first_tts_audio = performance.now();
}

function finalizePhrase(seq) {
  const entry = phraseBuffers.get(seq);
  if (!entry) return;
  entry.done = true;
  tryPlayAudio();
}

function tryPlayAudio() {
  while (phraseBuffers.has(nextSeqToPlay)) {
    const entry = phraseBuffers.get(nextSeqToPlay);
    if (!entry.done) break;
    const blob = new Blob(entry.chunks, { type: "audio/wav" });
    playbackQueue.push(blob);
    phraseBuffers.delete(nextSeqToPlay);
    nextSeqToPlay += 1;
  }
  if (!playing) {
    playNext();
  }
}

async function playNext() {
  if (!playbackQueue.length) {
    playing = false;
    return;
  }
  playing = true;
  const blob = playbackQueue.shift();
  const url = URL.createObjectURL(blob);
  player.src = url;
  currentAudioUrl = url;
  try {
    await player.play();
  } catch (err) {
    console.warn("Autoplay blocked", err);
  }
  player.onended = () => {
    URL.revokeObjectURL(url);
    currentAudioUrl = null;
    playing = false;
    playNext();
  };
}

function resetPlayback() {
  phraseBuffers.clear();
  playbackQueue.length = 0;
  nextSeqToPlay = 1;
  playing = false;
  player.pause();
  player.currentTime = 0;
  player.onended = null;
  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = null;
  }
  player.removeAttribute("src");
}

function updateMetrics() {
  if (!metrics.t0_user_audio_start) return;
  const lines = ["Latency (ms):"];
  const base = metrics.t0_user_audio_start;
  if (metrics.t1_first_partial) {
    lines.push(` first partial: ${(metrics.t1_first_partial - base).toFixed(1)}`);
  }
  if (metrics.t2_final_transcript) {
    lines.push(` final transcript: ${(metrics.t2_final_transcript - base).toFixed(1)}`);
  }
  if (metrics.t3_first_llm_token) {
    lines.push(` first token: ${(metrics.t3_first_llm_token - base).toFixed(1)}`);
  }
  if (metrics.t4_first_tts_audio) {
    lines.push(` first audio: ${(metrics.t4_first_tts_audio - base).toFixed(1)}`);
  }
  metricsPanel.textContent = lines.join("\n");
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

window.addEventListener("beforeunload", () => {
  teardownAudio();
  if (ws && ws.readyState === WebSocket.OPEN) ws.close();
});
