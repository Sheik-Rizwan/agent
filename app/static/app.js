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
  user: null,
  selectedConvId: null,
  turn: 0, // bumped on every new action so stale async callbacks can bail out
};

// ---------- helpers ----------

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// All API calls go through here. The session cookie is HttpOnly, so the page
// never sees the token; a 401 anywhere means "show the login screen".
async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ")
      : detail || `Request failed (${res.status})`;
    if (res.status === 401 && !options.allowUnauthorized) showLogin(message);
    throw new ApiError(message, res.status);
  }
  return data;
}

let toastTimer;
function toast(message, kind = "error") {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("ok", kind === "ok");
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
    const loaders = { history: loadHistory, users: loadUsers, agent: loadAgent };
    if (loaders[tab.dataset.view]) loaders[tab.dataset.view]();
  });
});

function showView(name) {
  document.querySelector(`.tab[data-view="${name}"]`).click();
}

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
    try { await api(`/api/v1/conversations/${encodeURIComponent(convId)}/end`, { method: "POST" }); }
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
    const { reply } = await api(`/api/v1/conversations/${encodeURIComponent(convId)}/messages`, {
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

// ---------- agent configuration (form generated from the API) ----------

// Human-readable label for a config key, e.g. "phone_not_heard" -> "Phone not heard".
function labelFor(key) {
  const text = key.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

async function loadAgent() {
  state.settings = await api("/api/v1/agent");
  $("#agent-title").textContent = state.settings.agent_name || "Agent";
  renderAgentForm(state.settings);
}

function renderAgentForm(settings) {
  const container = $("#agent-fields");
  container.innerHTML = "";
  const readOnly = !isAdmin();
  for (const [key, value] of Object.entries(settings)) {
    const label = document.createElement("label");
    label.textContent = labelFor(key);
    const short = key.endsWith("name");
    const field = document.createElement(short ? "input" : "textarea");
    field.name = key;
    field.value = value;
    field.required = true;
    field.maxLength = short ? 60 : 20000;
    field.readOnly = readOnly;
    if (!short) field.rows = key === "system_prompt" ? 16 : 2;
    label.appendChild(field);
    container.appendChild(label);
  }
}

$("#agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {};
  for (const field of $("#agent-fields").querySelectorAll("input, textarea")) {
    body[field.name] = field.value;
  }
  try {
    state.settings = await api("/api/v1/agent", { method: "PUT", body: JSON.stringify(body) });
    $("#agent-title").textContent = state.settings.agent_name;
    $("#agent-msg").textContent = "Saved. New calls use this configuration.";
  } catch (err) {
    toast(err.message);
  }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset every agent field to the defaults from config/agent.default.json?")) return;
  try {
    await api("/api/v1/agent/reset", { method: "POST" });
    await loadAgent();
    $("#agent-msg").textContent = "Reset to defaults.";
  } catch (err) {
    toast(err.message);
  }
});

$("#voice-select").addEventListener("change", (event) => {
  storageSet("agent.voice", event.target.value);
});

// ---------- history ----------

function shortId(id) {
  return id.slice(0, 8);
}

async function loadHistory() {
  const list = $("#conv-list");
  try {
    const convs = await api("/api/v1/conversations");
    list.innerHTML = "";
    if (!convs.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No conversations yet.";
      list.appendChild(li);
      return;
    }
    convs.forEach((c) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.dataset.id = c.id;
      btn.classList.toggle("active", c.id === state.selectedConvId);
      const label = c.channel === "phone" ? `Phone ${c.from_number || ""}`.trim() : `Web · ${c.created_by || "?"}`;
      const title = document.createElement("strong");
      title.textContent = `${label} · ${formatTime(c.started_at)}`;
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
    const data = await api(`/api/v1/conversations/${encodeURIComponent(id)}`);
    state.selectedConvId = id;
    document.querySelectorAll("#conv-list button").forEach((b) =>
      b.classList.toggle("active", b.dataset.id === id)
    );
    const via = data.channel === "phone"
      ? ` · phone ${data.from_number || "?"} → ${data.to_number || "?"}`
      : ` · web · ${data.created_by || "?"}`;
    $("#detail-title").textContent = `#${shortId(id)}${via}`;
    $("#delete-conv").classList.remove("hidden");
    const detail = $("#detail");
    detail.innerHTML = "";
    data.messages.forEach((m) => renderMessage(detail, m.role, m.content));
  } catch (err) {
    toast(err.message);
  }
}

$("#refresh-history").addEventListener("click", loadHistory);

$("#delete-conv").addEventListener("click", async () => {
  const id = state.selectedConvId;
  if (id === null || !confirm(`Delete conversation #${shortId(id)}? This can't be undone.`)) return;
  try {
    await api(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
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

// ---------- users (admin) ----------

async function loadUsers() {
  if (!isAdmin()) return;
  try {
    const users = await api("/api/v1/users");
    const tbody = $("#users-table tbody");
    tbody.innerHTML = "";
    users.forEach((u) => tbody.appendChild(userRow(u)));
  } catch (err) {
    toast(err.message);
  }
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function userRow(user) {
  const tr = document.createElement("tr");
  const isSelf = user.username === state.user.username;
  tr.append(
    cell(isSelf ? `${user.username} (you)` : user.username),
    cell(user.role),
    cell(formatTime(user.created_at)),
    cell(user.last_login_at ? formatTime(user.last_login_at) : "never"),
  );
  const actions = document.createElement("td");
  actions.className = "actions";

  const resetBtn = document.createElement("button");
  resetBtn.className = "btn small";
  resetBtn.type = "button";
  resetBtn.textContent = "Reset password";
  resetBtn.addEventListener("click", () => showResetForm(actions, user.username));
  actions.appendChild(resetBtn);

  if (!isSelf) {
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn small danger";
    deleteBtn.type = "button";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Delete user '${user.username}'? They will be logged out immediately.`)) return;
      try {
        await api(`/api/v1/users/${encodeURIComponent(user.username)}`, { method: "DELETE" });
        loadUsers();
      } catch (err) {
        toast(err.message);
      }
    });
    actions.appendChild(deleteBtn);
  }
  tr.appendChild(actions);
  return tr;
}

function showResetForm(container, username) {
  container.innerHTML = "";
  const form = document.createElement("form");
  form.className = "row";
  const input = document.createElement("input");
  input.type = "password";
  input.placeholder = "New password";
  input.autocomplete = "new-password";
  input.required = true;
  input.maxLength = 128;
  const save = document.createElement("button");
  save.className = "btn small primary";
  save.textContent = "Save";
  const cancel = document.createElement("button");
  cancel.className = "btn small";
  cancel.type = "button";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", loadUsers);
  form.append(input, save, cancel);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api(`/api/v1/users/${encodeURIComponent(username)}/password`, {
        method: "PUT",
        body: JSON.stringify({ password: input.value }),
      });
      if (username === state.user.username) {
        showLogin("Password changed. Please sign in again.");
        return;
      }
      toast(`Password updated for ${username}. They have been logged out.`, "ok");
      loadUsers();
    } catch (err) {
      toast(err.message);
    }
  });
  container.appendChild(form);
  input.focus();
}

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = {
    username: form.elements.username.value.trim().toLowerCase(),
    password: form.elements.password.value,
    role: form.elements.role.value,
  };
  try {
    const user = await api("/api/v1/users", { method: "POST", body: JSON.stringify(body) });
    form.reset();
    $("#user-msg").textContent = `Created ${user.role} '${user.username}'.`;
    loadUsers();
  } catch (err) {
    toast(err.message);
  }
});

// ---------- account ----------

$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.elements.new.value !== form.elements.repeat.value) {
    toast("The new passwords don't match.");
    return;
  }
  try {
    await api("/api/v1/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: form.elements.current.value,
        new_password: form.elements.new.value,
      }),
      allowUnauthorized: true, // a wrong current password is a 401 but shouldn't log you out
    });
    form.reset();
    $("#password-msg").textContent = "Password changed.";
  } catch (err) {
    toast(err.message);
  }
});

// ---------- login / logout ----------

function isAdmin() {
  return Boolean(state.user && state.user.role === "admin");
}

async function showLogin(message = "") {
  if (state.convId !== null) {
    state.turn++;
    stopListening();
    if (canSpeak) speechSynthesis.cancel();
    state.convId = null;
    setPhase("idle");
  }
  state.user = null;
  $("#app-shell").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  $("#login-msg").textContent = message === "Not logged in" ? "" : message;
  try {
    const { has_users } = await api("/api/v1/auth/setup-status", { allowUnauthorized: true });
    $("#setup-hint").classList.toggle("hidden", has_users);
  } catch { /* server unreachable; the login attempt will report it */ }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  $("#login-msg").textContent = "";
  try {
    const { user } = await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: form.elements.username.value, password: form.elements.password.value }),
      allowUnauthorized: true,
    });
    form.reset();
    enterApp(user);
  } catch (err) {
    $("#login-msg").textContent = err.message;
  }
});

$("#logout-btn").addEventListener("click", async () => {
  if (state.convId !== null) await endCall();
  try { await api("/api/v1/auth/logout", { method: "POST", allowUnauthorized: true }); } catch { /* ignore */ }
  showLogin("You have been logged out.");
});

async function enterApp(user) {
  state.user = user;
  document.body.classList.toggle("is-admin", isAdmin());
  $("#user-chip").textContent = `${user.username} · ${user.role}`;
  $("#login-view").classList.add("hidden");
  $("#app-shell").classList.remove("hidden");
  showView("talk");
  setPhase("idle");
  try {
    const status = await api("/api/v1/status");
    const badge = $("#mode-badge");
    badge.classList.remove("live", "demo");
    if (status.mode === "gemini") {
      badge.textContent = `Live · ${status.model}`;
      badge.classList.add("live");
    } else {
      badge.textContent = "Demo mode · no API key";
      badge.classList.add("demo");
    }
    await loadAgent();
    await loadTelephony();
  } catch (err) {
    if (err.status !== 401) toast(err.message);
  }
}

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
  try {
    const user = await api("/api/v1/auth/me", { allowUnauthorized: true });
    enterApp(user);
  } catch (err) {
    showLogin(err.status === 401 ? "" : `Cannot reach the server: ${err.message}`);
  }
}

init();
