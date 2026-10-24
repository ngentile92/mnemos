"use strict";
const { invoke } = window.__TAURI__.core;

// English or Spanish, following the system language (FTUE #198); English is the key, missing = English
const ES = /^es\b/i.test(navigator.language || "") ? {
  "Connected ✓": "Conectada ✓", "Waiting for the app…": "Esperando a la app…", "Not connected": "Sin conectar",
  "Connecting…": "Conectando…", "Open Claude": "Abrir Claude", "It can read and save your memory.": "Ya puede leer y guardar tu memoria.",
  "Did not work?": "¿No funcionó?", "Paste this once in Terminal instead:": "Pegá esto una vez en Terminal:", "Copy": "Copiar", "Copied ✓": "Copiado ✓",
  "Try again": "Reintentar", "Check again": "Volver a revisar", "Use the running Mnemos": "Usar el Mnemos abierto",
  "Pick your AIs": "Elegí tus IAs", "The AI apps found on this Mac. One click connects each one to your memory (all your spaces).": "Las IAs que encontramos en esta Mac. Un clic conecta cada una a tu memoria (todos tus espacios).",
  "Mnemos is running": "Mnemos está funcionando", "Mnemos is stopped": "Mnemos está detenido", "Starting…": "Iniciando…", "Open my memory": "Abrir mi memoria",
  "Restart the app and send it any message.": "Reiniciá la app y mandale cualquier mensaje.",
} : {};
const T = (s) => ES[s] || s;
const $ = (id) => document.getElementById(id);
let last = null;

async function refresh() {
  try { last = await invoke("status"); } catch (e) { $("err").textContent = String(e); return; }
  $("wizard").classList.toggle("hidden", last.initialized);
  $("main").classList.toggle("hidden", !last.initialized);
  const up = last.running && last.healthy;
  $("dot").className = "dot " + (up ? "ok" : last.running ? "" : "bad");
  $("state").textContent = up ? T("Mnemos is running") : last.running ? T("Starting…") : T("Mnemos is stopped");
  $("where").textContent = `Your memory lives in ${last.home}`;
  $("start").disabled = last.running; $("stop").disabled = !last.running; $("open").disabled = !up;
  $("details").textContent = JSON.stringify(last, null, 1);
  renderApps();
}

async function run(btn, fn) {
  btn.disabled = true; $("err").textContent = ""; $("ok").textContent = ""; $("fix").classList.add("hidden");
  try { await fn(); } catch (e) { showError(String(e)); }
  await refresh();
}
// every error says what happened and offers one button that fixes or retries it (#199)
function showError(msg) {
  $("err").textContent = msg;
  const f = $("fix");
  if (/another Mnemos is already running/i.test(msg)) { f.textContent = T("Use the running Mnemos"); f.onclick = (e) => run(e.target, async () => window.__TAURI__.opener.openUrl(await invoke("memory_url"))); }
  else if (/Port \d+ is used/i.test(msg)) { f.textContent = T("Try again"); f.onclick = (e) => run(e.target, () => invoke("start_hub")); }
  else if (/Could not find .* command/i.test(msg)) { f.textContent = T("Check again"); f.onclick = () => { cardsKey = ""; $("err").textContent = ""; f.classList.add("hidden"); refresh(); }; }
  else { f.textContent = T("Try again"); f.onclick = () => { $("err").textContent = ""; f.classList.add("hidden"); cardsKey = ""; refresh(); }; }
  f.classList.remove("hidden");
}

// Connect apps: only the apps found on this Mac, one button each. A card goes Connect → "Restart <app>" → Connected ✓,
// and the last step only shows once the app really talked to Mnemos (MCP handshake recorded by the hub).
const RESTART = {
  "claude-desktop": "Quit Claude Desktop (Cmd+Q), open it again and send it any message.",
  cursor: "Click Install in Cursor, then send it any message.",
  "claude-code": "Start a new Claude Code session and send it any message.",
  codex: "Start a new Codex session and send it any message.",
  vscode: "Reload VS Code and send a message in its chat.",
};
const pending = new Set();
let cardsKey = "";
function btn(text, cls, onclick) { const b = document.createElement("button"); b.textContent = text; if (cls) b.className = cls; b.onclick = onclick; return b; }
function para(text, cls) { const p = document.createElement("p"); p.className = cls || "muted small"; p.textContent = text; return p; }
function card(c) {
  const el = document.createElement("div"); el.className = "app";
  const head = document.createElement("div"); head.className = "head";
  const name = document.createElement("b"); name.textContent = c.name;
  const state = c.connected && c.seen ? "ok" : c.connected || pending.has(c.id) ? "restart" : "new";
  const badge = document.createElement("span"); badge.className = "badge" + (state === "ok" ? " on" : "");
  badge.textContent = state === "ok" ? T("Connected ✓") : state === "restart" ? T("Waiting for the app…") : T("Not connected");
  head.append(name, badge); el.append(head);
  const a = document.createElement("div"); a.className = "actions";
  if (state === "new") {
    a.append(btn(`Connect ${c.name}`, "primary", (e) => run(e.target, async () => {
      e.target.textContent = T("Connecting…");
      try { $("ok").textContent = await invoke("connect_app", { id: c.id }); pending.add(c.id); }
      catch (err) { el.dataset.failed = "1"; throw err; }
      finally { cardsKey = ""; }
    })));
  } else if (state === "restart") {
    el.append(para(RESTART[c.id] || T("Restart the app and send it any message.")));
    if (c.id === "claude-desktop") a.append(btn(T("Open Claude"), "", async () => { try { await invoke("open_claude"); } catch (_) { /* not installed */ } }));
  } else {
    el.append(para(T("It can read and save your memory.")));
  }
  if (a.childElementCount) el.append(a);
  if (c.command && state !== "ok") {
    // fallback if the button cannot run the app's command (kept out of sight: nobody should need Terminal)
    const d = document.createElement("details"); const s = document.createElement("summary"); s.className = "muted small"; s.textContent = T("Did not work?");
    const code = document.createElement("code"); code.textContent = c.command;
    d.append(s, para(T("Paste this once in Terminal instead:")), code, btn(T("Copy"), "", async (e) => { try { await navigator.clipboard.writeText(c.command); e.target.textContent = T("Copied ✓"); } catch (_) { e.target.textContent = "Select and Cmd+C"; } }));
    el.append(d);
  }
  if (c.id === "claude-desktop" && c.connected) claudeExtras(el);
  return el;
}
async function renderApps() {
  if (!last || !last.initialized) return;
  let cards;
  try { cards = await invoke("app_cards"); } catch (e) { return; }
  const key = JSON.stringify(cards) + [...pending].join();
  if (key === cardsKey) return;
  cardsKey = key;
  const found = cards.filter((c) => c.installed || c.connected), missing = cards.filter((c) => !c.installed && !c.connected);
  const nodes = found.map(card);
  if (!found.length) nodes.push(para("No AI app found on this Mac yet. Install Claude Desktop, Cursor, Claude Code, Codex or VS Code; it shows up here by itself. Using ChatGPT or Claude on the web? See “Later” below.", "small"));
  if (missing.length) {
    const d = document.createElement("details"); const s = document.createElement("summary"); s.className = "muted small";
    s.textContent = `Not on this Mac: ${missing.map((c) => c.name).join(", ")}`; d.append(s, para("Install one and it appears above."));
    nodes.push(d);
  }
  $("apps").replaceChildren(...nodes);
}
// Claude Desktop drops MCP server instructions: offer the text for its personal preferences, and flag a second
// (extension) copy of Mnemos.
async function claudeExtras(el) {
  let n; try { n = await invoke("claude_notes"); } catch (_) { return; }
  if (n.duplicate) {
    const w = document.createElement("p"); w.className = "small"; w.style.color = "#f59e0b";
    w.textContent = `Claude also has the old Mnemos extension (${n.extensions.join(", ")}), so it sees Mnemos twice. The extension is no longer needed: remove it in Claude → Settings → Extensions → Mnemos → Uninstall.`;
    const ob = document.createElement("button"); ob.textContent = "Open Claude to remove it";
    ob.onclick = async () => { try { await invoke("open_claude"); } catch (_) { /* Claude not installed */ } };
    const oa = document.createElement("div"); oa.className = "actions"; oa.append(ob);
    el.append(w, oa);
  }
  const p = document.createElement("p"); p.className = "muted small";
  p.textContent = "So Claude saves on its own: paste this once in Claude → Settings → Profile → personal preferences.";
  const code = document.createElement("code"); code.textContent = n.preferences;
  const b = document.createElement("button"); b.textContent = "Copy";
  b.onclick = async () => { try { await navigator.clipboard.writeText(n.preferences); b.textContent = "Copied ✓"; } catch (_) { b.textContent = "Select and Cmd+C"; } setTimeout(() => { b.textContent = "Copy"; }, 2000); };
  const a = document.createElement("div"); a.className = "actions"; a.append(b);
  el.append(p, code, a);
}
$("start").onclick = (e) => run(e.target, () => invoke("start_hub"));
$("stop").onclick = (e) => run(e.target, () => invoke("stop_hub"));
$("open").onclick = (e) => run(e.target, async () => window.__TAURI__.opener.openUrl(await invoke("memory_url")));
$("w-go").onclick = async (e) => {
  const pw = $("w-pw").value;
  $("w-err").textContent = "";
  if (pw.length < 8) return ($("w-err").textContent = "Password: at least 8 characters.");
  if (pw !== $("w-pw2").value) return ($("w-err").textContent = "Passwords do not match.");
  e.target.disabled = true; e.target.textContent = "Creating… ";
  try {
    await invoke("setup", { contexts: $("w-ctx").value.split(","), owner: $("w-owner").value, password: pw });
    $("w-pw").value = $("w-pw2").value = "";
    await invoke("start_hub");
  } catch (err) { $("w-err").textContent = String(err); }
  e.target.disabled = false; e.target.textContent = "Create my Mnemos";
  await refresh();
};
$("login").onchange = async (e) => { try { e.target.checked = await invoke("set_login_item", { enabled: e.target.checked }); } catch (err) { $("err").textContent = String(err); } };
invoke("login_item").then((v) => { $("login").checked = v; });
document.querySelectorAll("h3, p.muted.small, button").forEach((el) => { if (el.childElementCount === 0 && ES[el.textContent.trim()]) el.textContent = ES[el.textContent.trim()]; });
refresh();
setInterval(refresh, 2000);
