"use strict";
const { invoke } = window.__TAURI__.core;
const $ = (id) => document.getElementById(id);
let last = null;

async function refresh() {
  try { last = await invoke("status"); } catch (e) { $("err").textContent = String(e); return; }
  $("wizard").classList.toggle("hidden", last.initialized);
  $("main").classList.toggle("hidden", !last.initialized);
  const up = last.running && last.healthy;
  $("dot").className = "dot " + (up ? "ok" : last.running ? "" : "bad");
  $("state").textContent = up ? "Mnemos is running" : last.running ? "Starting…" : "Mnemos is stopped";
  $("where").textContent = `Your memory lives in ${last.home}`;
  $("start").disabled = last.running; $("stop").disabled = !last.running; $("open").disabled = !up;
  $("details").textContent = JSON.stringify(last, null, 1);
  renderApps();
}

async function run(btn, fn) {
  btn.disabled = true; $("err").textContent = ""; $("ok").textContent = "";
  try { await fn(); } catch (e) { $("err").textContent = String(e); }
  await refresh();
}

const HELP = {
  "claude-desktop": "Adds Mnemos to Claude Desktop. Then quit Claude (Cmd+Q) and reopen it.",
  cursor: "Opens Cursor; click Install there.",
  "claude-code": "Paste this in Terminal once:",
  codex: "Paste this in Terminal once:",
  vscode: "Paste this in Terminal once (or VS Code → MCP: Add Server):",
};
let cardsKey = "";
async function renderApps() {
  if (!last || !last.initialized) return;
  let cards;
  try { cards = await invoke("app_cards"); } catch (e) { return; }
  const key = JSON.stringify(cards);
  if (key === cardsKey) return;
  cardsKey = key;
  $("apps").replaceChildren(...cards.map((c) => {
    const el = document.createElement("div");
    el.className = "app";
    const head = document.createElement("div"); head.className = "head";
    const name = document.createElement("b"); name.textContent = c.name;
    const badge = document.createElement("span");
    badge.className = "badge" + (c.connected ? " on" : "");
    badge.textContent = c.connected ? "connected" : c.installed ? "not connected" : "not found on this Mac";
    head.append(name, badge);
    const p = document.createElement("p"); p.className = "muted small"; p.textContent = HELP[c.id] || "";
    el.append(head, p);
    if (c.how === "button" || c.how === "deeplink") {
      const b = document.createElement("button");
      b.textContent = c.connected ? `Reconnect ${c.name}` : `Connect ${c.name}`;
      b.onclick = (e) => run(e.target, async () => {
        $("ok").textContent = await invoke(c.how === "button" ? "connect_claude" : "connect_cursor");
        cardsKey = "";
      });
      const a = document.createElement("div"); a.className = "actions"; a.append(b); el.append(a);
      if (c.id === "claude-desktop") claudeExtras(el);
    } else if (c.command) {
      const code = document.createElement("code"); code.textContent = c.command;
      const b = document.createElement("button"); b.textContent = "Copy";
      b.onclick = async () => {
        try { await navigator.clipboard.writeText(c.command); b.textContent = "Copied ✓"; }
        catch (_) { const r = document.createRange(); r.selectNodeContents(code); getSelection().removeAllRanges(); getSelection().addRange(r); b.textContent = "Press Cmd+C"; }
        setTimeout(() => { b.textContent = "Copy"; }, 2000);
      };
      const a = document.createElement("div"); a.className = "actions"; a.append(b);
      el.append(code, a);
    }
    return el;
  }));
}
// Claude Desktop drops MCP server instructions: offer the text for its personal preferences, and flag a second
// (extension) copy of Mnemos.
async function claudeExtras(el) {
  let n; try { n = await invoke("claude_notes"); } catch (_) { return; }
  if (n.duplicate) {
    const w = document.createElement("p"); w.className = "small"; w.style.color = "#f59e0b";
    w.textContent = `Claude also has the Mnemos extension installed (${n.extensions.join(", ")}). Remove it in Claude → Settings → Extensions, so Claude sees one memory.`;
    el.append(w);
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
refresh();
setInterval(refresh, 2000);
