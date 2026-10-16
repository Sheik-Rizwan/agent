"use strict";

const $ = (sel) => document.querySelector(sel);

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const canListen = Boolean(SpeechRecognition);
const canSpeak = "speechSynthesis" in window;

const state = {
  convId: null,
  phase: "idle", // idle | listening | thinking | speaking
  recognition: null,
  utterance: null, // keep a reference so Chrome doesn't garbage-collect it mid-speech
  noSpeechRetries: 0,
  settings: null,
  selectedConvId: null,
  turn: 0, // bumped on every new action so stale async callbacks can bail out
};

// ---------- helpers ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 6000);
}

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

function formatTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Text-to-speech reads symbols aloud, so strip markdown the model may add.
function speakable(text) {
  return text.replace(/[*_#`>~]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1").trim();
}

// ---------- tabs ----------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".view").forEach((v) =>
      v.classList.toggle("active", v.id === `view-${tab.dataset.view}`)
    );
    if (tab.dataset.view === "history") loadHistory();
  });
});

// ---------- transcript ----------

function renderMessage(container, role, text, extraClass = "") {
  const empty = container.querySelector(".empty");
  if (empty) empty.remove();
  const el = document.createElement("div");
  el.className = `msg ${role} ${extraClass}`.trim();
  el.textContent = text;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

let interimEl = null;
function showInterim(text) {
  const transcript = $("#transcript");
  if (!interimEl) interimEl = renderMessage(transcript, "user", text, "interim");
  else interimEl.textContent = text;
  transcript.scrollTop = transcript.scrollHeight;
}
function clearInterim() {
  if (interimEl) interimEl.remove();
  interimEl = null;
}

// ---------- call state ----------

const STATUS_TEXT = {
  idle: () => (state.convId ? (canListen ? "Tap Talk to speak" : "Type a message below") : "Press Start call to begin"),
  listening: () => "Listening…",
  thinking: () => "Thinking…",
  speaking: () => "Speaking…",
};

function setPhase(phase) {
  state.phase = phase;
  $("#orb").className = `orb ${phase}`;
  $("#status").textContent = STATUS_TEXT[phase]();

  const inCall = state.convId !== null;
  const mic = $("#mic-btn");
  mic.disabled = !inCall || !canListen || phase === "thinking";
  mic.textContent = phase === "listening" ? "Stop" : "Talk";
  mic.classList.toggle("recording", phase === "listening");

  $("#start-btn").disabled = inCall;
  $("#end-btn").disabled = !inCall;
  $("#text-input").disabled = !inCall || phase === "thinking";
  $("#send-btn").disabled = !inCall || phase === "thinking";
}

async function startCall() {
  $("#start-btn").disabled = true;
  try {
    const conv = await api("/api/v1/conversations", { method: "POST" });
    state.convId = conv.id;
    const transcript = $("#transcript");
    transcript.innerHTML = "";
    renderMessage(transcript, "assistant", conv.greeting);
    setPhase("idle");
    const turn = ++state.turn;
    await speak(conv.greeting);
    if (turn === state.turn) afterAgentTurn();
  } catch (err) {
    toast(err.message);
    state.convId = null;
    setPhase("idle");
  }
}

async function endCall() {
  const convId = state.convId;
  state.turn++;
  stopListening();
  if (canSpeak) speechSynthesis.cancel();
  state.convId = null;
  setPhase("idle");
  renderMessage($("#transcript"), "system", "Call ended");
  if (convId !== null) {
    try { await api(`/api/v1/conversations/${convId}/end`, { method: "POST" }); }
    catch (err) { toast(err.message); }
  }
}

async function sendText(text) {
  text = text.trim();
  if (!text || state.convId === null) return;
  const convId = state.convId;
  const turn = ++state.turn;
  renderMessage($("#transcript"), "user", text);
  setPhase("thinking");
  try {
    const { reply } = await api(`/api/v1/conversations/${convId}/messages`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    if (turn !== state.turn) return; // call ended or caller moved on while waiting
    renderMessage($("#transcript"), "assistant", reply);
    await speak(reply);
    if (turn === state.turn) afterAgentTurn();
  } catch (err) {
    toast(err.message);
    if (turn === state.turn) setPhase("idle");
  }
}

function afterAgentTurn() {
  if (state.convId === null) return;
  if (canListen && $("#handsfree").checked) startListening();
  else setPhase("idle");
}

// ---------- speech output ----------

function pickVoice() {
  if (!canSpeak) return null;
  const voices = speechSynthesis.getVoices();
  const saved = storageGet("agent.voice");
  return voices.find((v) => v.name === saved) || voices.find((v) => v.default) || voices[0] || null;
}

function speak(text) {
  return new Promise((resolve) => {
    if (!canSpeak || state.convId === null) return resolve();
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(speakable(text));
    const voice = pickVoice();
    if (voice) { utterance.voice = voice; utterance.lang = voice.lang; }
    const done = () => {
      if (state.utterance === utterance) state.utterance = null;
      resolve();
    };
    utterance.onend = done;
    utterance.onerror = done;
    state.utterance = utterance;
    setPhase("speaking");
    speechSynthesis.speak(utterance);
  });
}

function populateVoices() {
  if (!canSpeak) return;
  const select = $("#voice-select");
  const voices = speechSynthesis.getVoices();
  if (!voices.length) return;
  const current = pickVoice();
  select.innerHTML = "";
  voices.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.name;
    opt.textContent = `${v.name} (${v.lang})`;
    if (current && v.name === current.name) opt.selected = true;
    select.appendChild(opt);
  });
}

// ---------- speech input ----------

function startListening() {
  if (!canListen || state.convId === null) return;
  state.turn++;
  if (canSpeak) speechSynthesis.cancel(); // let the caller interrupt the agent
  stopListening();

  const rec = new SpeechRecognition();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  let finalText = "";
  let error = null;

  rec.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) finalText += result[0].transcript;
      else interim += result[0].transcript;
    }
    showInterim((finalText + interim).trim());
  };
  rec.onerror = (event) => { error = event.error; };
  rec.onend = () => {
    if (state.recognition !== rec) return; // replaced or stopped deliberately
    state.recognition = null;
    clearInterim();
    if (state.convId === null) return;

    if (finalText.trim()) {
      state.noSpeechRetries = 0;
      sendText(finalText);
    } else if (error === "no-speech" && $("#handsfree").checked && state.noSpeechRetries < 2) {
      state.noSpeechRetries += 1;
      startListening();
    } else {
      state.noSpeechRetries = 0;
      if (error === "not-allowed" || error === "service-not-allowed") {
        toast("Microphone access was blocked. Allow it in the browser's address bar, then try again.");
      } else if (error && error !== "no-speech" && error !== "aborted") {
        toast(`Speech recognition error: ${error}`);
      }
      setPhase("idle");
    }
  };

  state.recognition = rec;
  try {
    rec.start();
    setPhase("listening");
  } catch (err) {
    state.recognition = null;
    toast(`Could not start the microphone: ${err.message}`);
    setPhase("idle");
  }
}

function stopListening() {
  const rec = state.recognition;
  state.recognition = null;
  clearInterim();
  if (rec) {
    try { rec.abort(); } catch { /* already stopped */ }
  }
}

// ---------- settings ----------

async function loadSettings() {
  state.settings = await api("/api/v1/agent");
  const form = $("#settings-form");
  for (const key of ["agent_name", "greeting", "system_prompt"]) {
    form.elements[key].value = state.settings[key];
  }
  $("#agent-title").textContent = state.settings.agent_name;
}

$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = {
    agent_name: form.elements.agent_name.value,
    greeting: form.elements.greeting.value,
    system_prompt: form.elements.system_prompt.value,
  };
  try {
    state.settings = await api("/api/v1/agent", { method: "PUT", body: JSON.stringify(body) });
    $("#agent-title").textContent = state.settings.agent_name;
    $("#settings-msg").textContent = "Saved. New calls use this configuration.";
  } catch (err) {
    toast(err.message);
  }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset the agent name, greeting and prompt to the defaults?")) return;
  try {
    await api("/api/v1/agent/reset", { method: "POST" });
    await loadSettings();
    $("#settings-msg").textContent = "Reset to defaults.";
  } catch (err) {
    toast(err.message);
  }
});

$("#voice-select").addEventListener("change", (event) => {
  storageSet("agent.voice", event.target.value);
});

// ---------- history ----------

async function loadHistory() {
  const list = $("#conv-list");
  try {
    const convs = await api("/api/v1/conversations");
    list.innerHTML = "";
    if (!convs.length) {
      list.innerHTML = '<li class="muted">No conversations yet.</li>';
      return;
    }
    convs.forEach((c) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.dataset.id = c.id;
      btn.classList.toggle("active", c.id === state.selectedConvId);
      const title = document.createElement("strong");
      const label = c.channel === "phone" ? `Phone ${c.from_number || ""}`.trim() : "Web";
      title.textContent = `#${c.id} · ${label} · ${formatTime(c.started_at)}`;
      const preview = document.createElement("span");
      preview.className = "preview";
      preview.textContent = `${c.message_count} messages · ${c.first_user_message || "no caller messages"}`;
      btn.append(title, preview);
      btn.addEventListener("click", () => showConversation(c.id));
      li.appendChild(btn);
      list.appendChild(li);
    });
  } catch (err) {
    toast(err.message);
  }
}

async function showConversation(id) {
  try {
    const data = await api(`/api/v1/conversations/${id}`);
    state.selectedConvId = id;
    document.querySelectorAll("#conv-list button").forEach((b) =>
      b.classList.toggle("active", Number(b.dataset.id) === id)
    );
    const via = data.channel === "phone" ? ` · phone call ${data.from_number || ""} → ${data.to_number || ""}` : " · web";
    $("#detail-title").textContent = `Conversation #${id}${via}`;
    $("#delete-conv").classList.remove("hidden");
    const detail = $("#detail");
    detail.innerHTML = "";
    data.messages.forEach((m) => renderMessage(detail, m.role, m.content));
  } catch (err) {
    toast(err.message);
  }
}

$("#delete-conv").addEventListener("click", async () => {
  const id = state.selectedConvId;
  if (id === null || !confirm(`Delete conversation #${id}?`)) return;
  try {
    await api(`/api/v1/conversations/${id}`, { method: "DELETE" });
    state.selectedConvId = null;
    $("#detail").innerHTML = "";
    $("#detail-title").textContent = "Select a conversation";
    $("#delete-conv").classList.add("hidden");
    loadHistory();
  } catch (err) {
    toast(err.message);
  }
});

// ---------- phone calls (Plivo) ----------

async function loadTelephony() {
  try {
    const status = await api("/api/v1/calls/status");
    const ready = status.configured && Boolean(status.public_base_url);
    $("#call-number").disabled = !ready;
    $("#call-btn").disabled = !ready;
    $("#phone-hint").textContent = !status.configured
      ? "Add your Plivo credentials to .env to enable phone calls."
      : !status.public_base_url
        ? "Set PUBLIC_BASE_URL in .env (e.g. your ngrok URL) so Plivo can reach this server."
        : `Calls come from ${status.phone_number}. Callers to that number also reach the agent.`;
  } catch (err) {
    $("#phone-hint").textContent = err.message;
  }
}

$("#call-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const to = $("#call-number").value.trim();
  if (!to) return;
  $("#call-btn").disabled = true;
  try {
    await api("/api/v1/calls", { method: "POST", body: JSON.stringify({ to }) });
    $("#phone-hint").textContent = `Calling ${to}… The transcript will appear in History.`;
  } catch (err) {
    toast(err.message);
  } finally {
    $("#call-btn").disabled = false;
  }
});

// ---------- wiring ----------

$("#start-btn").addEventListener("click", startCall);
$("#end-btn").addEventListener("click", endCall);
$("#mic-btn").addEventListener("click", () => {
  if (state.phase === "listening") {
    // Stop and send whatever was heard so far.
    if (state.recognition) state.recognition.stop();
  } else {
    state.noSpeechRetries = 0;
    startListening();
  }
});
$("#text-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#text-input");
  const text = input.value;
  input.value = "";
  stopListening();
  if (canSpeak) speechSynthesis.cancel();
  sendText(text);
});

async function init() {
  if (!canListen) $("#speech-warning").classList.remove("hidden");
  if (canSpeak) {
    populateVoices();
    speechSynthesis.onvoiceschanged = populateVoices;
  }
  setPhase("idle");
  try {
    const health = await api("/api/v1/health");
    const badge = $("#mode-badge");
    if (health.mode === "gemini") {
      badge.textContent = `Live · ${health.model}`;
      badge.classList.add("live");
    } else {
      badge.textContent = "Demo mode · no API key";
      badge.classList.add("demo");
    }
    await loadSettings();
    await loadTelephony();
  } catch (err) {
    toast(`Cannot reach the server: ${err.message}`);
  }
}

init();
