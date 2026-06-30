"use strict";
// G1 Interactive control panel — vanilla JS, no build step.

const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (id) => document.getElementById(id);

async function api(path, { method = "GET", body = null } = {}) {
  const headers = {};
  if (TOKEN) headers["X-Panel-Token"] = TOKEN;
  if (body !== null) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body !== null ? JSON.stringify(body) : null });
  if (!res.ok) {
    let detail = res.status;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = TOKEN ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : "";
  return `${proto}://${location.host}${path}${q}`;
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}
function showRestart() { $("restartBanner").classList.remove("hidden"); }

// ---- process control ----
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const badge = $("statusBadge");
    if (s.running) { badge.textContent = "● running (" + s.mode + ")"; badge.className = "badge badge-run"; }
    else { badge.textContent = "○ stopped"; badge.className = "badge badge-stop"; }
    $("dashStatus").textContent = s.running ? "Running" : "Stopped";
    $("dashMode").textContent = s.detail || "";
    $("dashSystemd").textContent = s.systemd_available
      ? "systemd is available — you can install the pipeline as a user service."
      : "systemd not detected — the panel manages the pipeline as a subprocess.";
    $("btnInstallService").style.display = s.systemd_available ? "" : "none";
    // When stopped, surface WHY (captured stdout/stderr or journal tail) so a failed
    // launch isn't a silent "stopped".
    const errWrap = $("dashErrorWrap");
    if (!s.running && s.recent_output) {
      $("dashError").textContent = s.recent_output;
      errWrap.classList.remove("hidden");
    } else {
      errWrap.classList.add("hidden");
    }
  } catch (e) { /* panel reachable check handled elsewhere */ }
}
async function procAction(path) {
  try { await api(path, { method: "POST" }); toast("OK"); refreshStatus(); }
  catch (e) { toast("Failed: " + e.message, true); }
}

// ---- tabs ----
const tabInit = {};
function switchTab(name) {
  document.querySelectorAll(".navItem").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  closeStreams(name);
  if (tabInit[name]) tabInit[name]();
}

// ---- console ----
let logWS = null, logPaused = false;
function closeLogWS() { if (logWS) { logWS.onclose = null; logWS.close(); logWS = null; } }
function openLogWS() {
  closeLogWS();
  const file = $("logFile").value;
  logWS = new WebSocket(wsUrl("/ws/logs?file=" + file));
  logWS.onmessage = (ev) => {
    if (logPaused) return;
    const data = JSON.parse(ev.data);
    const view = $("logView");
    view.textContent += data.line + "\n";
    view.scrollTop = view.scrollHeight;
  };
}
tabInit.console = async () => {
  $("logView").textContent = "";
  openLogWS();
  try { $("logLevel").value = (await api("/api/loglevel")).level; } catch (_) {}
};

// ---- conversation ----
let convWS = null;
function closeConvWS() { if (convWS) { convWS.onclose = null; convWS.close(); convWS = null; } }
function renderTurn(ev) {
  const wrap = $("transcript");
  const u = document.createElement("div");
  u.className = "bubble user";
  u.textContent = ev.user || "";
  const b = document.createElement("div");
  b.className = "bubble bot";
  b.innerHTML = `${escapeHtml(ev.reply || "")}<div class="meta">` +
    `<span class="chip">${ev.lang || "?"}</span><span class="chip">${ev.emotion || ""}</span>` +
    `${ev.from_kb ? '<span class="chip">KB</span>' : ""}<span class="chip">${ev.ms_total || 0} ms</span></div>`;
  wrap.appendChild(u); wrap.appendChild(b);
  wrap.scrollTop = wrap.scrollHeight;
}
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
async function loadCost() {
  try {
    const c = await api("/api/cost");
    $("dashTurns").textContent = c.turns;
    $("dashCost").textContent = "$" + c.totals.total_usd;
    $("costBreakdown").innerHTML =
      `<div>STT <b>$${c.totals.stt_usd}</b></div><div>LLM <b>$${c.totals.llm_usd}</b></div>` +
      `<div>TTS <b>$${c.totals.tts_usd}</b></div><div>Total <b>$${c.totals.total_usd}</b></div>` +
      `<div class="muted">${c.turns} turns · estimate</div>`;
    renderPriceForm(c.prices);
  } catch (e) { $("costBreakdown").textContent = "cost unavailable"; }
}
function renderPriceForm(prices) {
  $("priceForm").innerHTML = Object.entries(prices).map(([k, v]) =>
    `<label>${k}<input data-price="${k}" type="number" step="0.0001" value="${v}"></label>`).join("");
}
tabInit.conversation = async () => {
  $("transcript").innerHTML = "";
  try { (await api("/api/transcript?lines=50")).turns.forEach(renderTurn); } catch (_) {}
  await loadCost();
  closeConvWS();
  convWS = new WebSocket(wsUrl("/ws/transcript"));
  convWS.onmessage = (ev) => { renderTurn(JSON.parse(ev.data)); loadCost(); };
};

// ---- knowledge ----
let kbCurrent = null;
async function loadKbList() {
  const data = await api("/api/kb");
  $("kbList").innerHTML = data.files.map((f) =>
    `<li data-name="${f.name}"><div>${f.name}</div><div class="fmeta">${f.bytes} B</div></li>`).join("")
    || '<li class="muted">no files</li>';
  document.querySelectorAll("#kbList li[data-name]").forEach((li) =>
    li.onclick = () => openKb(li.dataset.name));
}
async function openKb(name) {
  const data = await api("/api/kb/" + encodeURIComponent(name));
  kbCurrent = name;
  $("kbName").textContent = name;
  $("kbContent").value = data.content;
  $("btnKbSave").disabled = false; $("btnKbDelete").disabled = false;
  document.querySelectorAll("#kbList li").forEach((li) => li.classList.toggle("active", li.dataset.name === name));
}
tabInit.knowledge = loadKbList;

// ---- instructions ----
tabInit.instructions = async () => { $("personaContent").value = (await api("/api/persona")).content; };

// ---- gestures ----
function gestureOptions(catalog, selectedId) {
  let html = catalog.map((c) =>
    `<option value="${c.id}" ${c.id === selectedId ? "selected" : ""}>${c.name} (#${c.id})</option>`).join("");
  // Preserve a custom id (set via the Environment tab) that isn't in the catalog,
  // so saving the Gestures tab doesn't silently clobber it with the first option.
  if (selectedId != null && !catalog.some((c) => c.id === selectedId)) {
    html = `<option value="${selectedId}" selected>Custom (#${selectedId})</option>` + html;
  }
  return html;
}
tabInit.gestures = async () => {
  const g = await api("/api/gestures");
  const talkId = (g.talk_ids && g.talk_ids.length) ? g.talk_ids[0] : null;
  $("talkGesture").innerHTML = gestureOptions(g.catalog, talkId);
  $("wakeGesture").innerHTML = gestureOptions(g.catalog, g.wake_id);
  try {
    const m = await api("/api/movement");
    $("mvEnabled").checked = !!m.enabled;
    $("mvSpeed").value = m.speed;
    $("mvYaw").value = m.yaw;
    $("mvDur").value = m.duration_s;
  } catch (_) {}
};

// ---- models (selectable LLM provider/model + TTS voice) ----
let modelsCfg = null;
function renderLlmNote() {
  if (!modelsCfg) return;
  const p = (modelsCfg.llm.presets || []).find((x) => x.id === $("mdlLlm").value);
  if (!p) { $("mdlLlmNote").textContent = ""; return; }
  const key = p.key_name
    ? (p.key_set ? `✓ ${p.key_name} is set.` : `⚠️ ${p.key_name} is not set — set it in the Environment tab or this provider can't run.`)
    : "Custom configuration (set via the Environment tab).";
  $("mdlLlmNote").textContent = `${p.backend} · ${p.model}   —   ${key}`;
}
function renderTtsNote() {
  if (!modelsCfg) return;
  const m = (modelsCfg.tts.models || []).find((x) => x.id === $("mdlTts").value);
  $("mdlTtsNote").textContent = m && m.note ? m.note : "";
}
tabInit.models = async () => {
  modelsCfg = await api("/api/models");
  $("mdlLlm").innerHTML = (modelsCfg.llm.presets || []).map((p) =>
    `<option value="${escapeAttr(p.id)}" ${p.id === modelsCfg.llm.preset ? "selected" : ""}>` +
    `${escapeHtml(p.label)}${p.key_name && !p.key_set ? " ⚠️" : ""}</option>`).join("");
  $("mdlTts").innerHTML = (modelsCfg.tts.models || []).map((m) =>
    `<option value="${escapeAttr(m.id)}" ${m.id === modelsCfg.tts.model ? "selected" : ""}>` +
    `${escapeHtml(m.label)}</option>`).join("");
  renderLlmNote();
  renderTtsNote();
};

// ---- speech (latency toggles) ----
tabInit.speech = async () => {
  const s = await api("/api/speech");
  $("spStreaming").checked = !!s.streaming;
  $("spChunking").checked = !!s.chunking;
  $("spChunkChars").value = s.chunk_max_chars;
  $("spSttBackend").value = s.stt_backend || "openai";
  $("spNoiseMin").value = s.noise_min_chars;
  $("spEndAnnounce").checked = !!s.end_announce;
};

// ---- dialogflow (answer-first toggle + live test) ----
tabInit.dialogflow = async () => {
  const d = await api("/api/dialogflow");
  $("dfEnabled").checked = !!d.enabled;
  $("dfProject").value = d.project || "";
  $("dfLocation").value = d.location || "";
  $("dfAgent").value = d.agent_id || "";
  $("dfKey").value = d.key_path || "";
  $("dfConf").value = d.confidence;
  $("dfTestOut").textContent = "";
};

// ---- web search ----
tabInit.search = async () => {
  const s = await api("/api/search");
  $("wsEnabled").checked = !!s.enabled;
  $("wsCount").value = s.count;
  $("wsAnnEn").value = s.announce_en || "";
  $("wsAnnAr").value = s.announce_ar || "";
  $("wsKeyState").textContent = s.key_set
    ? "✓ Brave API key is set."
    : "⚠️ No BRAVE_SEARCH_API_KEY — set it in the Environment tab, or search stays off.";
  $("wsTestOut").textContent = "";
};

// ---- memory (the robot's brain) ----
let memCurrent = null;
function renderMemStats(s) {
  if (!s) { $("memStats").textContent = "—"; return; }
  const types = Object.entries(s.by_type || {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none";
  $("memStats").textContent =
    `${s.total} memories (${types}) · ${s.expiring_soon} expiring soon · ${s.sessions} session snapshots`;
}
async function loadMemList() {
  const data = await api("/api/memory/list");
  $("memList").innerHTML = (data.memories || []).map((m) =>
    `<li data-id="${m.id}"><div>${escapeHtml(m.subject || m.id)} <span class="chip">${m.type}</span></div>` +
    `<div class="fmeta">sal ${Number(m.salience).toFixed(2)} · exp ${m.expiry}</div></li>`).join("")
    || '<li class="muted">no memories yet</li>';
  document.querySelectorAll("#memList li[data-id]").forEach((li) =>
    li.onclick = () => openMem(li.dataset.id));
}
async function openMem(id) {
  const data = await api("/api/memory/item?id=" + encodeURIComponent(id));
  if (!data.ok) { toast(data.detail || "could not open", true); return; }
  memCurrent = id;
  $("memName").textContent = id;
  $("memContent").value = data.content;
  $("btnMemSave").disabled = false; $("btnMemDelete").disabled = false;
  document.querySelectorAll("#memList li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
}
async function loadSessionList() {
  const data = await api("/api/memory/sessions");
  $("memSessionList").innerHTML = (data.sessions || []).map((s) =>
    `<li data-id="${s.id}"><div>${s.id}</div><div class="fmeta">${s.turn_count} turns · ${(s.ended_at || "").replace("T", " ")}</div></li>`).join("")
    || '<li class="muted">no sessions yet</li>';
  document.querySelectorAll("#memSessionList li[data-id]").forEach((li) =>
    li.onclick = () => openSession(li.dataset.id));
}
async function openSession(id) {
  const data = await api("/api/memory/session?id=" + encodeURIComponent(id));
  $("memSessionName").textContent = id;
  if (!data.ok) { $("memSessionView").textContent = "✗ " + (data.detail || "failed"); return; }
  const t = (data.session.transcript || []).map((x) =>
    `${x.role === "user" ? "Visitor" : "Robot"}: ${x.content}`).join("\n");
  $("memSessionView").textContent = t || "(empty)";
  document.querySelectorAll("#memSessionList li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
}
tabInit.memory = async () => {
  const c = await api("/api/memory");
  $("memLongTerm").checked = !!c.long_term_enabled;
  $("memSnapshots").checked = !!c.session_snapshots;
  $("memVisitorTtl").value = c.visitor_ttl_days;
  $("memFactTtl").value = c.fact_ttl_days;
  $("memRecallK").value = c.recall_k;
  renderMemStats(c.stats);
  memCurrent = null;
  $("memName").textContent = "Select a memory…"; $("memContent").value = "";
  $("btnMemSave").disabled = true; $("btnMemDelete").disabled = true;
  $("memSessionName").textContent = "Select a session…"; $("memSessionView").textContent = "";
  await loadMemList();
  await loadSessionList();
};

// ---- vision (peek) ----
tabInit.vision = async () => {
  const v = await api("/api/vision");
  $("vsCamera").checked = !!v.camera_enabled;
  $("vsPeek").checked = !!v.peek_enabled;
  $("vsUrl").value = v.snapshot_url || "";
  $("vsModel").value = v.vision_model || "";
  $("vsAnnEn").value = v.announce_en || "";
  $("vsAnnAr").value = v.announce_ar || "";
  $("vsTestOut").textContent = "";
};

// ---- teleop mode (master override) ----
tabInit.teleop = async () => {
  const t = await api("/api/teleop");
  $("tpEnabled").checked = !!t.enabled;
  $("tpSummary").textContent = t.enabled
    ? "Teleop mode is ON — arm gestures, voice movement and the head camera are released for the teleoperator (your saved settings are kept and resume when you turn it off)."
    : "Teleop mode is OFF — the robot uses your normal gesture / movement / camera settings.";
};

// ---- environment ----
const VOICE_KEYS = ["ELEVENLABS_VOICE_ID", "ELEVENLABS_ARABIC_VOICE_ID"];
tabInit.environment = async () => {
  const data = await api("/api/env");
  $("envGroups").innerHTML = data.groups.map((g) => {
    const fields = g.fields.map((f) => {
      const voice = VOICE_KEYS.includes(f.key);
      const ph = f.secret ? (f.masked_value || "not set") : "";
      const val = f.secret ? "" : escapeAttr(f.value);
      return `<div class="envField ${voice ? "voice" : ""}">
        <label>${f.key}${voice ? ' <span class="tag">voice</span>' : ""}${f.secret ? ' <span class="tag">secret</span>' : ""}</label>
        <input data-key="${f.key}" data-secret="${f.secret}" type="text" value="${val}" placeholder="${escapeAttr(ph)}">
        ${f.comment ? `<div class="help">${escapeHtml(f.comment)}</div>` : ""}</div>`;
    }).join("");
    return `<div class="envGroup"><h3>${g.title}</h3>${fields}</div>`;
  }).join("");
};
function escapeAttr(s) { return String(s == null ? "" : s).replace(/"/g, "&quot;"); }
async function saveEnv() {
  const updates = {};
  document.querySelectorAll("#envGroups input[data-key]").forEach((inp) => {
    const secret = inp.dataset.secret === "true";
    if (secret && inp.value === "") return;       // unchanged secret
    updates[inp.dataset.key] = inp.value;
  });
  try { const r = await api("/api/env", { method: "POST", body: { updates } }); toast("Environment saved"); if (r.restart_required) showRestart(); }
  catch (e) { toast("Save failed: " + e.message, true); }
}

// ---- scripts ----
let scriptWS = null;
async function loadScripts() {
  const data = await api("/api/scripts");
  $("scriptList").innerHTML = data.scripts.map((s) =>
    `<li data-name="${s.name}" data-dir="${s.dir}"><div>${s.name}</div><div class="fmeta">${s.dir}${s.desc ? " · " + escapeHtml(s.desc) : ""}</div></li>`).join("")
    || '<li class="muted">no scripts</li>';
  document.querySelectorAll("#scriptList li[data-name]").forEach((li) =>
    li.onclick = () => runScript(li.dataset.name, li.dataset.dir));
}
async function runScript(name, dir) {
  $("scriptOutput").textContent = "";
  $("scriptRunning").textContent = "Running " + name + " …";
  try {
    const { run_id } = await api("/api/scripts/run", { method: "POST", body: { name, dir } });
    if (scriptWS) scriptWS.close();
    scriptWS = new WebSocket(wsUrl("/ws/script/" + run_id));
    scriptWS.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      const out = $("scriptOutput");
      if (d.done !== undefined) { $("scriptRunning").textContent = name + " finished (exit " + d.code + ")"; }
      else { out.textContent += d.line + "\n"; out.scrollTop = out.scrollHeight; }
    };
  } catch (e) { toast("Run failed: " + e.message, true); $("scriptRunning").textContent = "—"; }
}
tabInit.scripts = loadScripts;

function closeStreams(keep) {
  if (keep !== "console") closeLogWS();
  if (keep !== "conversation") closeConvWS();
}

// ---- wiring ----
function wire() {
  document.querySelectorAll(".navItem").forEach((b) => b.onclick = () => switchTab(b.dataset.tab));
  $("btnStart").onclick = () => procAction("/api/start");
  $("btnStop").onclick = () => procAction("/api/stop");
  $("btnRestart").onclick = () => procAction("/api/restart");
  $("btnBannerRestart").onclick = () => { procAction("/api/restart"); $("restartBanner").classList.add("hidden"); };
  $("btnInstallService").onclick = async () => {
    try { const r = await api("/api/service/install", { method: "POST" }); toast(r.detail); refreshStatus(); }
    catch (e) { toast("Install failed: " + e.message, true); }
  };

  $("logFile").onchange = openLogWS;
  $("btnLogPause").onclick = () => { logPaused = !logPaused; $("btnLogPause").textContent = logPaused ? "Resume" : "Pause"; };
  $("btnLogClear").onclick = () => { $("logView").textContent = ""; };
  $("logLevel").onchange = async () => {
    try { const r = await api("/api/loglevel", { method: "POST", body: { level: $("logLevel").value } }); if (r.restart_required) showRestart(); toast("Log level set"); }
    catch (e) { toast("Failed: " + e.message, true); }
  };

  $("btnKbSave").onclick = async () => {
    if (!kbCurrent) return;
    try { await api("/api/kb/" + encodeURIComponent(kbCurrent), { method: "PUT", body: { content: $("kbContent").value } }); toast("Saved " + kbCurrent); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnKbDelete").onclick = async () => {
    if (!kbCurrent || !confirm("Delete " + kbCurrent + "?")) return;
    try { await api("/api/kb/" + encodeURIComponent(kbCurrent), { method: "DELETE" }); kbCurrent = null; $("kbContent").value = ""; $("kbName").textContent = "Select a file…"; loadKbList(); }
    catch (e) { toast("Delete failed: " + e.message, true); }
  };
  $("btnKbNew").onclick = async () => {
    const name = prompt("New knowledge file name (.md/.txt):", "new.md");
    if (!name) return;
    try { const r = await api("/api/kb", { method: "POST", body: { name, content: "" } }); await loadKbList(); openKb(r.name); }
    catch (e) { toast("Create failed: " + e.message, true); }
  };

  $("btnPersonaSave").onclick = async () => {
    try { const r = await api("/api/persona", { method: "PUT", body: { content: $("personaContent").value } }); toast("Instructions saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("btnGesturesSave").onclick = async () => {
    const talk_ids = [parseInt($("talkGesture").value, 10)];
    const wake_id = parseInt($("wakeGesture").value, 10);
    try { const r = await api("/api/gestures", { method: "POST", body: { talk_ids, wake_id } }); toast("Gestures saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("btnMovementSave").onclick = async () => {
    const body = {
      enabled: $("mvEnabled").checked,
      speed: parseFloat($("mvSpeed").value),
      yaw: parseFloat($("mvYaw").value),
      duration_s: parseFloat($("mvDur").value),
    };
    try { const r = await api("/api/movement", { method: "POST", body }); toast("Movement settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("btnTeleopSave").onclick = async () => {
    try {
      const r = await api("/api/teleop", { method: "POST", body: { enabled: $("tpEnabled").checked } });
      toast("Teleop mode saved");
      tabInit.teleop();
      if (r.restart_required) showRestart();
    } catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("mdlLlm").onchange = renderLlmNote;
  $("mdlTts").onchange = renderTtsNote;
  $("btnModelsSave").onclick = async () => {
    const body = { llm_preset: $("mdlLlm").value, tts_model: $("mdlTts").value };
    try { const r = await api("/api/models", { method: "POST", body }); toast("Models saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("btnSpeechSave").onclick = async () => {
    const body = {
      streaming: $("spStreaming").checked,
      chunking: $("spChunking").checked,
      chunk_max_chars: parseInt($("spChunkChars").value, 10),
      stt_backend: $("spSttBackend").value,
      noise_min_chars: parseInt($("spNoiseMin").value, 10),
      end_announce: $("spEndAnnounce").checked,
    };
    try { const r = await api("/api/speech", { method: "POST", body }); toast("Speech settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("btnDfSave").onclick = async () => {
    const body = {
      enabled: $("dfEnabled").checked,
      project: $("dfProject").value.trim(),
      location: $("dfLocation").value.trim(),
      agent_id: $("dfAgent").value.trim(),
      key_path: $("dfKey").value.trim(),
      confidence: parseFloat($("dfConf").value),
    };
    try { const r = await api("/api/dialogflow", { method: "POST", body }); toast("Dialogflow settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnDfTest").onclick = async () => {
    const q = $("dfTestQ").value;
    if (!q.trim()) { toast("Type a question to test", true); return; }
    $("dfTestOut").textContent = "Testing…";
    try {
      const r = await api("/api/dialogflow/test", { method: "POST", body: { query: q } });
      if (!r.ok) { $("dfTestOut").textContent = "✗ " + (r.detail || "failed"); return; }
      const hit = r.match_type === "INTENT" ? "✓ matched" : "✗ no confident intent";
      $("dfTestOut").textContent =
        `${hit}\nlang: ${r.lang}   match: ${r.match_type}   intent: ${r.intent || "-"}   conf: ${r.confidence}\n\n${r.answer || "(no answer)"}`;
    } catch (e) { $("dfTestOut").textContent = "✗ " + e.message; }
  };

  $("btnSearchSave").onclick = async () => {
    const body = {
      enabled: $("wsEnabled").checked,
      count: parseInt($("wsCount").value, 10),
      announce_en: $("wsAnnEn").value,
      announce_ar: $("wsAnnAr").value,
    };
    try { const r = await api("/api/search", { method: "POST", body }); toast("Web-search settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnSearchTest").onclick = async () => {
    const q = $("wsTestQ").value;
    if (!q.trim()) { toast("Type a query to test", true); return; }
    $("wsTestOut").textContent = "Searching…";
    try {
      const r = await api("/api/search/test", { method: "POST", body: { query: q } });
      if (!r.ok) { $("wsTestOut").textContent = "✗ " + (r.detail || "failed"); return; }
      const lines = (r.results || []).map((x, i) => `${i + 1}. ${x.title}\n   ${x.description}`).join("\n\n");
      $("wsTestOut").textContent = `✓ ${r.count} result(s) (${r.lang})\n\n${lines || "(none)"}`;
    } catch (e) { $("wsTestOut").textContent = "✗ " + e.message; }
  };

  $("btnMemorySave").onclick = async () => {
    const body = {
      long_term: $("memLongTerm").checked,
      session_snapshots: $("memSnapshots").checked,
      visitor_ttl_days: parseInt($("memVisitorTtl").value, 10),
      fact_ttl_days: parseInt($("memFactTtl").value, 10),
      recall_k: parseInt($("memRecallK").value, 10),
    };
    try { const r = await api("/api/memory", { method: "POST", body }); toast("Memory settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnForgetVisitors").onclick = async () => {
    if (!confirm("Forget ALL visitor memories now? (teams & supervisors are kept)")) return;
    try { const r = await api("/api/memory/forget-visitors", { method: "POST" }); toast("Forgot " + (r.removed || 0) + " visitor(s)"); tabInit.memory(); }
    catch (e) { toast("Failed: " + e.message, true); }
  };
  $("btnMemSave").onclick = async () => {
    if (!memCurrent) return;
    try { const r = await api("/api/memory/item", { method: "POST", body: { id: memCurrent, content: $("memContent").value } });
      if (!r.ok) { toast(r.detail || "save failed", true); return; } toast("Saved " + memCurrent); loadMemList(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnMemDelete").onclick = async () => {
    if (!memCurrent || !confirm("Delete memory " + memCurrent + "?")) return;
    try { await api("/api/memory/delete", { method: "POST", body: { id: memCurrent } });
      memCurrent = null; $("memContent").value = ""; $("memName").textContent = "Select a memory…";
      $("btnMemSave").disabled = true; $("btnMemDelete").disabled = true; loadMemList(); }
    catch (e) { toast("Delete failed: " + e.message, true); }
  };

  $("btnVisionSave").onclick = async () => {
    const body = {
      camera_enabled: $("vsCamera").checked,
      peek_enabled: $("vsPeek").checked,
      snapshot_url: $("vsUrl").value,
      vision_model: $("vsModel").value,
      announce_en: $("vsAnnEn").value,
      announce_ar: $("vsAnnAr").value,
    };
    try { const r = await api("/api/vision", { method: "POST", body }); toast("Vision settings saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };
  $("btnVisionTest").onclick = async () => {
    $("vsTestOut").textContent = "Capturing…";
    try {
      const r = await api("/api/vision/test", { method: "POST", body: {} });
      $("vsTestOut").textContent = r.ok
        ? `✓ got a frame — ${r.bytes} bytes${r.jpeg ? " (JPEG)" : ""}\n${r.url}`
        : "✗ " + (r.detail || "failed");
    } catch (e) { $("vsTestOut").textContent = "✗ " + e.message; }
  };

  $("btnEnvSave").onclick = saveEnv;

  $("btnSavePrices").onclick = async () => {
    const prices = {};
    document.querySelectorAll("#priceForm input[data-price]").forEach((i) => prices[i.dataset.price] = parseFloat(i.value));
    try { await api("/api/prices", { method: "POST", body: { prices } }); toast("Prices saved"); loadCost(); }
    catch (e) { toast("Save failed: " + e.message, true); }
  };

  $("scriptUpload").onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    const headers = TOKEN ? { "X-Panel-Token": TOKEN } : {};
    try { const res = await fetch("/api/scripts", { method: "POST", headers, body: fd }); if (!res.ok) throw new Error(res.status); toast("Uploaded"); loadScripts(); }
    catch (err) { toast("Upload failed: " + err.message, true); }
  };

  refreshStatus();
  setInterval(refreshStatus, 4000);
}

wire();
