"use strict";
// App memories (0.13): each AI app's own memory files, read-only, exactly as stored; import a section into Mnemos.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const t = (x) => (window.t ? window.t(x) : x);
  let data = null, app = null, file = null;
  function msg(t, bad) { $("nm-msg").textContent = t; $("nm-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  const when = (s) => (s ? new Date(s).toLocaleString() : "");
  const spaces = () => (((window.MNEMOS_SPACES || {}).spaces) || []).filter((s) => s.name !== "shared");
  async function load() {
    $("nm-files").innerHTML = '<p class="muted small"><span class="spin"></span>Looking at your apps…</p>';
    try { data = await window.MNEMOS_API("/api/native"); } catch (e) { return msg(e.message, true); }
    const n = data.apps.reduce((a, x) => a + x.items.length, 0);
    $("nm-head").textContent = `${n} file${n === 1 ? "" : "s"} · read-only`;
    if (!app || !data.apps.find((a) => a.key === app)) app = (data.apps.find((a) => a.items.length) || data.apps[0]).key;
    render();
  }
  function render() {
    $("nm-apps").innerHTML = data.apps.map((a) => `<button class="mbtn nm-app ${a.key === app ? "primary" : ""}" data-k="${esc(a.key)}">${esc(a.label)} <span class="muted">${a.items.length}</span></button>`).join("");
    const a = data.apps.find((x) => x.key === app);
    $("nm-files").innerHTML = a.items.length ? a.items.map((it, i) => `<a class="nm-file ${file === it.path ? "on" : ""}" data-i="${i}">
        <b>${esc(it.kind)}</b>${it.mnemos ? ' <span class="badge">written by Mnemos</span>' : ""}<br><span class="small muted">${esc(it.where)}</span><br>
        <span class="small muted">modified ${esc(when(it.modified))}</span></a>`).join("")
      : `<p class="muted small">${esc(a.label)} keeps no memory files on this computer (or it is not installed).</p>`;
    if (!file && a.items.length) file = a.items[0].path;  // never an empty viewer: open the first file
    const it = a.items.find((x) => x.path === file);
    if (!it) { $("nm-view").innerHTML = `<div class="empty"><b>${esc(t("Nothing to show yet"))}</b>${esc(t("When this app writes memory files on this computer, they appear here exactly as stored."))}</div>`; return; }
    const pick = `<select class="nm-sp" aria-label="Space"><option value="auto">Sort by content</option>${spaces().map((s) => `<option value="${esc(s.name)}">${esc(s.label)}</option>`).join("")}</select>`;
    $("nm-view").innerHTML = `<div class="small muted nm-meta"><code>${esc(it.where)}</code> · modified ${esc(when(it.modified))} · ${it.size} characters${it.truncated ? " (showing the start)" : ""}</div>
      ${it.note ? `<p class="small">${esc(it.note)}</p>` : ""}
      ${it.readonly_list ? `<pre class="nm-raw">${esc(it.content)}</pre>` : it.sections.map((s, i) => `<div class="nm-sec" data-s="${i}">
        <div class="nm-sec-h"><b>${esc(s.title)}</b><span style="margin-left:auto"></span>${it.mnemos ? "" : `${pick}<button class="mbtn nm-imp">Import into Mnemos</button>`}</div>
        <pre class="nm-raw">${esc(s.text)}</pre></div>`).join("")}`;
  }
  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    if (!t.closest || !t.closest("#view-native")) return;
    if (t.id === "nm-refresh") return load();
    const b = t.closest(".nm-app"); if (b) { app = b.dataset.k; file = null; return render(); }
    const f = t.closest(".nm-file"); if (f) { file = data.apps.find((x) => x.key === app).items[+f.dataset.i].path; return render(); }
    if (t.classList.contains("nm-imp")) {
      const sec = t.closest(".nm-sec"); t.disabled = true;
      try {
        const r = await window.MNEMOS_API("/api/native/import", { path: file, section: +sec.dataset.s, app, context: sec.querySelector(".nm-sp").value });
        const lab = (spaces().find((s) => s.name === r.context) || {}).label || r.context;
        t.textContent = r.saved ? `Imported → ${lab}` : "Already in Mnemos"; msg(r.saved ? `Saved to ${lab}${r.route && r.route.reason ? ` (${r.route.reason})` : ""}.` : "You already had that.");
      } catch (e) { t.disabled = false; msg(e.message, true); }
    }
  });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "native" && !data) load(); });
})();
