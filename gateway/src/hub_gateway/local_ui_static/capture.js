"use strict";
// Apps → Auto-capture: install/remove the capture hooks of Claude Code, Cursor and Codex.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let data = null;
  function msg(t, bad) { $("cap-msg").textContent = t; $("cap-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  async function load() {
    try { data = await window.MNEMOS_API("/api/capture/hooks"); } catch (e) { return msg(e.message, true); }
    const n = Object.values(data.spool || {}).reduce((a, b) => a + b, 0);
    $("cap-head").textContent = n ? `${n} messages waiting to be read` : "";
    $("cap-list").innerHTML = Object.entries(data.apps).map(([k, a]) => `<div class="ap-row" data-k="${esc(k)}"><div class="ap-top"><div><b>${esc(a.label)}</b>
        ${a.installed ? '<span class="ap-pill">on</span>' : ""}${a.app_found ? "" : ' <span class="muted small">(not found on this computer)</span>'}</div>
        <span class="small muted">${esc(a.file)}${k === "codex" && a.installed ? " · in Codex, type /hooks once and trust the Mnemos hooks" : ""}</span></div>
        <div class="ap-ctl"><button class="mbtn ${a.installed ? "" : "primary"} cap-tg">${a.installed ? "Turn off" : "Turn on"}</button></div></div>`).join("");
  }
  $("cap-list").addEventListener("click", async (ev) => {
    if (!ev.target.classList.contains("cap-tg")) return;
    const k = ev.target.closest(".ap-row").dataset.k, on = !data.apps[k].installed;
    try { await window.MNEMOS_API("/api/capture/hooks", { app: k, on }); msg(on ? `Auto-capture on for ${data.apps[k].label}. Restart it to pick it up.` : `Auto-capture off for ${data.apps[k].label}.`); load(); }
    catch (e) { msg(e.message, true); }
  });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "apps") load(); });
})();
