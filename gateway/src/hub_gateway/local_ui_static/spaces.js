"use strict";
// Spaces screen: name, colour, one-line description per space, Shared explained, read-only bridges as toggles.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let data = null;
  const T = (x) => (window.tr ? window.tr(x) : x);
  const dot = (sp) => `<span class="sp-dot" style="background:${esc(sp.color)}"></span>`;
  function msg(t, bad) { $("sp-msg").textContent = t; $("sp-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  async function load() {
    try { data = await window.MNEMOS_API("/api/spaces"); } catch (e) { return msg(e.message, true); }
    window.MNEMOS_SPACES = data;
    try { routing = await window.MNEMOS_API("/api/routing"); } catch { routing = null; }
    if (routing) data.spaces.forEach((sp) => { const d = routing.spaces.find((x) => x.name === sp.name); if (d && !d.custom) sp.default_description = d.description; });
    render(); folders();
    window.dispatchEvent(new Event("mnemos:spaces"));
  }
  let routing = null;
  const lab = (n) => ((data.spaces.find((s) => s.name === n) || {}).label || n);
  function folders() {
    if (!routing || !$("sp-folders")) return;
    $("sp-fspace").innerHTML = data.spaces.filter((s) => s.name !== "shared").map((s) => `<option value="${esc(s.name)}">${esc(s.label)}</option>`).join("");
    $("sp-folders").innerHTML = routing.folders.length ? routing.folders.map((f) => `<div class="sp-br small"><code>${esc(f.path)}</code>&nbsp;→&nbsp;<b>${esc(lab(f.space))}</b>
      <button class="mbtn sp-frm" data-p="${esc(f.path)}" style="margin-left:auto">Remove</button></div>`).join("") : '<div class="empty"><b>No folders yet</b>Add a project folder to send everything said there to one space.</div>';
  }
  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    try {
      if (t.id === "sp-fadd") { routing = await window.MNEMOS_API("/api/routing/folder", { path: $("sp-fpath").value, space: $("sp-fspace").value }); $("sp-fpath").value = ""; folders(); msg("Folder mapped."); }
      else if (t.id === "sp-add") {
        t.disabled = true;
        try {
          data = await window.MNEMOS_API("/api/spaces/add", { label: $("sp-new-label").value, description: $("sp-new-desc").value });
          window.MNEMOS_SPACES = data; $("sp-new-label").value = $("sp-new-desc").value = "";
          render(); msg("Space added. Your AIs can use it from their next message."); setTimeout(() => location.reload(), 1500);  // every space picker gets it
        } finally { t.disabled = false; }
      }
      else if (t.classList && t.classList.contains("sp-frm")) { routing = await window.MNEMOS_API("/api/routing/folder", { path: t.dataset.p, space: "" }); folders(); msg("Removed."); }
      else if (t.id === "sp-ttry") {
        $("sp-tout").innerHTML = '<span class="spin"></span>';
        const r = await window.MNEMOS_API("/api/routing/test", { text: $("sp-ttext").value });
        $("sp-tout").innerHTML = `→ <b>${esc(lab(r.space))}</b> · ${esc(r.reason)} <span class="muted">(${esc(r.by)}, ${Math.round(r.confidence * 100)}%)</span>${r.review ? ` · <span class="badge">review</span>${r.suggested ? ` maybe ${esc(lab(r.suggested))}` : ""}` : ""}`;
      }
    } catch (e) { msg(e.message, true); $("sp-tout").textContent = ""; }
  });
  function render() {
    // 0.19: one space = one memory to name; sharing, bridges and folders only appear once there are two
    const many = data.spaces.filter((s) => s.name !== "shared").length > 1;
    $("sp-intro-one").classList.toggle("hidden", many); $("sp-intro-many").classList.toggle("hidden", !many);
    document.querySelectorAll("#view-spaces .sp-many").forEach((el) => el.classList.toggle("hidden", !many));
    $("sp-shared").textContent = data.shared;
    $("sp-list").innerHTML = data.spaces.map((sp, i) => `<div class="sp-row" data-i="${i}">
      <div class="sp-sw">${data.palette.map((c) => `<button title="${c}" data-c="${c}" class="${c === sp.color ? "on" : ""}" style="background:${c}"></button>`).join("")}</div>
      <div><input class="sp-label" maxlength="40" value="${esc(sp.label === "My memory" ? T(sp.label) : sp.label)}" aria-label="Name">${many ? `<div class="sp-key">key: ${esc(sp.name)} (never changes)</div>` : ""}</div>
      <input class="sp-desc${many ? "" : " hidden"}" maxlength="200" value="${esc(sp.description === sp.name ? "" : sp.description)}" placeholder="${esc(sp.default_description || "What goes here, in one line (e.g. my job at Acme: clients, team, projects)")}" aria-label="Description">
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
      const desc = row.querySelector(".sp-desc").value.trim();
      data = await window.MNEMOS_API("/api/spaces/update", { name: sp.name, label: row.querySelector(".sp-label").value,
        color: sp.color, ...(desc ? { description: desc } : {}) });
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
