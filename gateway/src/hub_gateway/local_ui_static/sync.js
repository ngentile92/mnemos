"use strict";
// 0.12: opt-in native memory files for coding agents (Claude Code rules, Codex AGENTS.md block, Cursor plugin rule).
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("sync-card")) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (path, body) => window.MNEMOS_API(path, body);
  function render(j) {
    $("sync-list").innerHTML = j.targets.map((t) => `<div class="me-item" data-t="${esc(t.id)}"><div class="m"><b>${esc(t.label)}</b>
      <span class="muted small">${esc(t.path)}${t.installed ? "" : " · not found on this computer"}</span></div>
      <div class="small">${j.spaces.map((s) => `<label style="margin-right:10px"><input type="checkbox" class="sy-sp" value="${esc(s)}" style="width:auto" ${t.spaces.includes(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}
      <label style="margin-left:12px"><input type="checkbox" class="sy-on" style="width:auto" ${t.on ? "checked" : ""}> On</label>
      ${t.written_at ? `<span class="muted"> · updated ${esc(t.written_at.replace("T", " ").slice(0, 16))}</span>` : ""}
      ${t.backup ? `<span class="muted"> · backup kept</span>` : ""}</div></div>`).join("");
    $("sync-list").querySelectorAll("[data-t]").forEach((row) => {
      const send = async () => {
        const spaces = [...row.querySelectorAll(".sy-sp:checked")].map((x) => x.value);
        try { const r = await api("/api/sync", { target: row.dataset.t, on: row.querySelector(".sy-on").checked, spaces });
          $("sync-msg").textContent = r.error || (r.removed ? "Removed ✓" : "Written ✓ " + (r.path || "")); render(r); }
        catch (e) { $("sync-msg").textContent = e.message; }
      };
      row.querySelector(".sy-on").onchange = send;
      row.querySelectorAll(".sy-sp").forEach((x) => { x.onchange = () => { if (row.querySelector(".sy-on").checked) send(); }; });
    });
  }
  async function load() { try { render(await api("/api/sync")); } catch (e) { $("sync-msg").textContent = e.message; } }
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "apps") load(); });
})();
