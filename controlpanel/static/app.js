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
tabInit.gestures = async () => {
  const g = await api("/api/gestures");
  const talk = new Set(g.talk_ids);
  $("talkGestures").innerHTML = g.catalog.map((c) =>
    `<label class="gesture-item"><input type="checkbox" data-gid="${c.id}" ${talk.has(c.id) ? "checked" : ""}> ${c.name} <span class="muted">#${c.id}</span></label>`).join("");
  $("wakeGesture").innerHTML = g.catalog.map((c) =>
    `<option value="${c.id}" ${c.id === g.wake_id ? "selected" : ""}>${c.name} (#${c.id})</option>`).join("");
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
    const talk_ids = [...document.querySelectorAll('#talkGestures input[data-gid]:checked')].map((i) => parseInt(i.dataset.gid, 10));
    const wake_id = parseInt($("wakeGesture").value, 10);
    try { const r = await api("/api/gestures", { method: "POST", body: { talk_ids, wake_id } }); toast("Gestures saved"); if (r.restart_required) showRestart(); }
    catch (e) { toast("Save failed: " + e.message, true); }
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
