const SAMPLE_RATE = 16000;
const CHUNK_MS = 160;

const recordBtn = document.getElementById("recordBtn");
const stopBtn = document.getElementById("stopBtn");
const cancelBtn = document.getElementById("cancelBtn");
const partialPanel = document.getElementById("partial");
const finalPanel = document.getElementById("final");
const tokensPanel = document.getElementById("tokens");
const metricsPanel = document.getElementById("metrics");
const statusDot = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const livePill = document.getElementById("livePill");
const latencyChip = document.getElementById("latencyChip");
const timeline = document.getElementById("timeline");
const clearMetricsBtn = document.getElementById("clearMetricsBtn");
const themeToggle = document.getElementById("themeToggle");
const themeLabel = document.getElementById("themeLabel");
const logsPanel = document.getElementById("logsPanel");
const clearLogsBtn = document.getElementById("clearLogsBtn");
const cadenceSelect = document.getElementById("cadenceSelect");

let ws;
let audioContext;
let workletNode;
let mediaStream;
let recording = false;
let metrics = {};
let totalAudioDurationMs = 0;

let pendingAssistant = "";
const conversation = [];
const logsBuffer = [];
const THEME_KEY = "voice_prompt_theme";
const CADENCE_KEY = "voice_prompt_cadence";

recordBtn.addEventListener("click", () => startRecording());
stopBtn.addEventListener("click", () => stopRecording());
cancelBtn.addEventListener("click", () => sendCancel());
clearMetricsBtn.addEventListener("click", () => {
  metrics = {};
  totalAudioDurationMs = 0;
  metricsPanel.textContent = "";
  latencyChip.textContent = "Latency —";
});

if (clearLogsBtn) {
  clearLogsBtn.addEventListener("click", () => {
    logsBuffer.length = 0;
    renderLogs();
  });
}

function restoreSelect(select, key) {
  if (!select) return;
  const stored = localStorage.getItem(key);
  if (stored && [...select.options].some((option) => option.value === stored)) {
    select.value = stored;
  }
  select.addEventListener("change", () => {
    localStorage.setItem(key, select.value);
  });
}
restoreSelect(cadenceSelect, CADENCE_KEY);

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  if (themeLabel) {
    themeLabel.textContent = theme === "light" ? "Light" : "Dark";
  }
}

function loadTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

const initialTheme = loadTheme();
applyTheme(initialTheme);

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const next = document.body.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem(THEME_KEY, next);
  });
}

function setStatus(state) {
  statusDot.classList.remove("active", "busy");
  switch (state) {
    case "recording":
      statusDot.classList.add("active");
      statusLabel.textContent = "Recording";
      break;
    case "responding":
      statusDot.classList.add("busy");
      statusLabel.textContent = "Responding";
      break;
    case "idle":
    default:
      statusLabel.textContent = "Idle";
      break;
  }
}

function setLiveState(text) {
  livePill.textContent = text;
}

function pushConversation(role, text) {
  if (!text.trim()) return;
  conversation.push({ role, text: text.trim() });
  while (conversation.length > 12) {
    conversation.shift();
  }
  renderTimeline();
}

function renderTimeline() {
  timeline.innerHTML = "";
  conversation.forEach((item) => {
    const li = document.createElement("li");
    li.className = "timeline-item";
    const role = document.createElement("span");
    role.className = "role";
    role.textContent = item.role;
    const text = document.createElement("p");
    text.className = "text";
    text.textContent = item.text;
    li.append(role, text);
    timeline.append(li);
  });
}

function appendLog(message) {
  logsBuffer.push(message);
  if (logsBuffer.length > 200) {
    logsBuffer.splice(0, logsBuffer.length - 200);
  }
  renderLogs();
}

function renderLogs() {
  if (!logsPanel) return;
  logsPanel.textContent = logsBuffer.join("\n");
  logsPanel.scrollTop = logsPanel.scrollHeight;
}

async function startRecording() {
  if (recording) return;
  await ensureWebSocket();
  await setupAudio();
  recording = true;
  recordBtn.disabled = true;
  stopBtn.disabled = false;
  cancelBtn.disabled = false;
  partialPanel.textContent = "";
  finalPanel.textContent = "";
  tokensPanel.textContent = "";
  metricsPanel.textContent = "";
  metrics = { t0_user_audio_start: performance.now() };
  pendingAssistant = "";
  setStatus("recording");
  setLiveState("Listening");
  const startPayload = { type: "start", sample_rate: SAMPLE_RATE };
  if (cadenceSelect && cadenceSelect.value) {
    startPayload.cadence = cadenceSelect.value;
  }
  ws.send(JSON.stringify(startPayload));
}

async function stopRecording() {
  if (!recording) return;
  recording = false;
  teardownAudio();
  recordBtn.disabled = false;
  stopBtn.disabled = true;
  cancelBtn.disabled = true;
  ws.send(JSON.stringify({ type: "stop" }));
  setLiveState("Processing");
}

async function sendCancel() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "cancel" }));
  cancelBtn.disabled = true;
  setStatus("idle");
  setLiveState("Cancelled");
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
    recordBtn.disabled = false;
    stopBtn.disabled = true;
    cancelBtn.disabled = true;
    setStatus("idle");
    setLiveState("Disconnected");
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
    totalAudioDurationMs += (data.byteLength / 2 / SAMPLE_RATE) * 1000;
  };
}

function handleServerMessage(event) {
  const data = typeof event.data === "string" ? JSON.parse(event.data) : null;
  if (!data) return;
  switch (data.type) {
    case "partial_transcript":
      partialPanel.textContent = data.text;
      if (!metrics.t1_first_partial) metrics.t1_first_partial = performance.now();
      setLiveState("Listening");
      break;
    case "final_transcript":
      finalPanel.textContent = data.text;
      if (!metrics.t2_final_transcript) metrics.t2_final_transcript = performance.now();
      pushConversation("User", data.text);
      setStatus("responding");
      setLiveState("Processing");
      if (!metrics.audio_duration_ms && totalAudioDurationMs) {
        metrics.audio_duration_ms = totalAudioDurationMs;
        totalAudioDurationMs = 0;
      }
      break;
    case "llm_token":
      if (!data.done) {
        pendingAssistant += data.text;
        tokensPanel.textContent = pendingAssistant;
        if (!metrics.t3_first_llm_token) metrics.t3_first_llm_token = performance.now();
        setLiveState("Responding");
      } else {
        if (pendingAssistant) {
          pushConversation("Assistant", pendingAssistant);
          pendingAssistant = "";
        }
        setStatus("idle");
        setLiveState("Idle");
      }
      break;
    case "reply_complete":
      setStatus("idle");
      setLiveState("Idle");
      break;
    case "info":
      console.log("info", data.message);
      break;
    case "error":
      console.error("error", data.message);
      break;
    case "log":
      appendLog(data.message);
      break;
  }
  updateMetrics();
}

function updateMetrics() {
  if (!metrics.t0_user_audio_start) return;
  const lines = ["Latency (MM:SS.ss):"];
  const base = metrics.t0_user_audio_start;
  if (metrics.t1_first_partial) {
    lines.push(` first partial: ${formatDuration(metrics.t1_first_partial - base)}`);
  }
  if (metrics.t2_final_transcript) {
    lines.push(` final transcript: ${formatDuration(metrics.t2_final_transcript - base)}`);
  }
  if (metrics.t3_first_llm_token) {
    lines.push(` first token: ${formatDuration(metrics.t3_first_llm_token - base)}`);
  }
  if (metrics.audio_duration_ms) {
    lines.push(` audio captured: ${formatDuration(metrics.audio_duration_ms)}`);
  }
  metricsPanel.textContent = lines.join("\n");
  const latency = metrics.t3_first_llm_token || metrics.t2_final_transcript;
  latencyChip.textContent = latency
    ? `Latency ${formatDuration(latency - base)}`
    : "Latency —";
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return "0:00.00";
  const totalSeconds = ms / 1000;
  const minutes = Math.floor(totalSeconds / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (totalSeconds % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${seconds}`;
}

window.addEventListener("beforeunload", () => {
  teardownAudio();
  if (ws && ws.readyState === WebSocket.OPEN) ws.close();
});
