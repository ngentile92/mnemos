"use strict";
// Spaces screen: name, colour, one-line description per space, Shared explained, read-only bridges as toggles.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let data = null;
  const dot = (sp) => `<span class="sp-dot" style="background:${esc(sp.color)}"></span>`;
  function msg(t, bad) { $("sp-msg").textContent = t; $("sp-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  async function load() {
    try { data = await window.MNEMOS_API("/api/spaces"); } catch (e) { return msg(e.message, true); }
    window.MNEMOS_SPACES = data;
    render();
    window.dispatchEvent(new Event("mnemos:spaces"));
  }
  function render() {
    $("sp-shared").textContent = data.shared;
    $("sp-list").innerHTML = data.spaces.map((sp, i) => `<div class="sp-row" data-i="${i}">
      <div class="sp-sw">${data.palette.map((c) => `<button title="${c}" data-c="${c}" class="${c === sp.color ? "on" : ""}" style="background:${c}"></button>`).join("")}</div>
      <div><input class="sp-label" maxlength="40" value="${esc(sp.label)}" aria-label="Name"><div class="sp-key">key: ${esc(sp.name)} (never changes)</div></div>
      <input class="sp-desc" maxlength="200" value="${esc(sp.description)}" placeholder="What goes here, in one line (e.g. my job at Acme: clients, team, projects)" aria-label="Description">
      <button class="mbtn primary sp-save">Save</button></div>`).join("");
    const pairs = [];
    for (const a of data.spaces) for (const b of data.spaces) if (a.name !== b.name) pairs.push([a, b]);
    $("sp-bridges").innerHTML = pairs.map(([a, b]) => {
      const on = data.bridges.some((x) => x.from === a.name && x.to === b.name);
      return `<label class="sp-br small"><input type="checkbox" data-from="${esc(a.name)}" data-to="${esc(b.name)}" ${on ? "checked" : ""} style="width:auto">
        ${dot(b)}<b>${esc(b.label)}</b>&nbsp;can read&nbsp;${dot(a)}<b>${esc(a.label)}</b></label>`;
    }).join("") || '<p class="small muted">Add a second space to use this.</p>';
  }
  $("sp-list").addEventListener("click", async (ev) => {
    const row = ev.target.closest(".sp-row");
    if (!row) return;
    const sp = data.spaces[+row.dataset.i];
    if (ev.target.dataset.c) {
      row.querySelectorAll(".sp-sw button").forEach((b) => b.classList.toggle("on", b === ev.target));
      sp.color = ev.target.dataset.c;
      return save(row, sp);
    }
    if (ev.target.classList.contains("sp-save")) save(row, sp);
  });
  async function save(row, sp) {
    try {
      data = await window.MNEMOS_API("/api/spaces/update", { name: sp.name, label: row.querySelector(".sp-label").value,
        color: sp.color, description: row.querySelector(".sp-desc").value });
      window.MNEMOS_SPACES = data;
      render(); msg("Saved. Your AI apps see it from their next message."); window.dispatchEvent(new Event("mnemos:spaces"));
    } catch (e) { msg(e.message, true); }
  }
  $("sp-bridges").addEventListener("change", async (ev) => {
    const t = ev.target;
    try {
      data = await window.MNEMOS_API("/api/spaces/bridge", { from: t.dataset.from, to: t.dataset.to, on: t.checked });
      render(); msg(t.checked ? "On: read-only, it can never change those notes." : "Off.");
    } catch (e) { t.checked = !t.checked; msg(e.message, true); }
  });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "spaces") load(); });
  // labels and colours for the other screens (context pickers show the space's name)
  const boot = setInterval(() => {
    if (!window.MNEMOS_API || !document.getElementById("app") || document.getElementById("app").classList.contains("hidden")) return;
    clearInterval(boot);
    load().then(() => {
      for (const sel of document.querySelectorAll("select")) {
        for (const o of sel.options) {
          const sp = data && data.spaces.find((s) => s.name === o.value);
          if (sp && o.textContent === sp.name) { o.value = sp.name; o.textContent = sp.label; }
        }
      }
    });
  }, 300);
})();
