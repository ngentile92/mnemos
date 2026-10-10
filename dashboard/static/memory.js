"use strict";
// Memory tab: browse notes per context, see provenance + history, correct, forget, undo.
// Edits go through the gateway's own tools (memory_update/delete/undo), so the ledger keeps every version.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("view-memoria")) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CTXS = (window.MNEMOS?.contexts || []).map((c) => c.name);
  let items = [], loaded = false;
  $("me-ctx").innerHTML = CTXS.map((c) => `<option>${esc(c)}</option>`).join("");
  const ctx = () => $("me-ctx").value;

  async function get(path, params) {
    const r = await fetch(path + "?" + new URLSearchParams(params));
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
    return j;
  }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
    return j;
  }
  async function load() {
    loaded = true;
    $("me-list").innerHTML = '<p class="muted">loading…</p>';
    try {
      const j = await get("/api/memory/list", { context: ctx(), contains: $("me-q").value, include_shared: $("me-shared").checked ? "1" : "0" });
      items = j.items || [];
      $("me-count").textContent = `${items.length} of ${j.total ?? items.length}`;
      $("me-list").innerHTML = items.length ? items.map((it, i) =>
        `<div class="note" data-i="${i}"><div class="small muted">${esc(it.dataset)} · ${esc((it.created_at || "").slice(0, 16))} · ${esc(it.source_app || "")}${it.editable ? "" : " · read-only"}</div>` +
        `<div>${esc((it.text || "").slice(0, 160))}</div></div>`).join("") : '<p class="muted">No notes.</p>';
    } catch (e) { $("me-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  async function show(it) {
    const d = $("me-detail");
    const prov = it.provenance ? `<pre class="small">${esc(JSON.stringify(it.provenance, null, 1))}</pre>` : "";
    d.innerHTML = `<div class="small muted">id ${esc(it.id)} · ${esc(it.dataset)}</div>` +
      (it.editable ? `<textarea id="me-text" rows="10" maxlength="20000">${esc(it.text)}</textarea>
        <div><button id="me-save">Save correction</button> <button id="me-forget">Forget</button> <button id="me-undo">Undo last change</button></div>`
        : `<pre>${esc(it.text)}</pre><p class="muted small">shared notes are edited by the owner with scripts/memory_admin.py</p>`) +
      `<h3>Provenance</h3>${prov || '<p class="muted small">none recorded</p>'}<h3>History</h3><div id="me-hist" class="small muted">loading…</div><p id="me-msg" class="small"></p>`;
    if (it.editable) {
      const act = async (path, body, ok) => {
        try { const j = await post(path, { context: ctx(), id: it.id, ...body }); $("me-msg").textContent = ok(j); await load(); }
        catch (e) { $("me-msg").textContent = "error: " + e.message; }
      };
      $("me-save").onclick = () => act("/api/memory/update", { text: $("me-text").value }, () => "saved (previous version kept)");
      $("me-forget").onclick = () => confirm("Forget this note? You can undo it.") && act("/api/memory/delete", {}, () => "forgotten — Undo restores it");
      $("me-undo").onclick = () => act("/api/memory/undo", {}, (j) => "undone" + (j.id && j.id !== it.id ? ` (restored as ${j.id})` : ""));
    }
    try {
      const h = await get("/api/memory/history", { context: ctx(), id: it.id });
      const v = h.previous_versions || [];
      $("me-hist").innerHTML = v.length ? v.map((x) => `<div><b>${esc(x.action)}</b> ${esc(x.replaced_at || "")} ${esc(x.by || "")}<pre>${esc((x.text || "").slice(0, 400))}</pre></div>`).join("") : "no previous versions";
    } catch (e) { $("me-hist").textContent = e.message; }
  }
  $("me-list").addEventListener("click", (ev) => { const n = ev.target.closest(".note"); if (n) show(items[+n.dataset.i]); });
  $("me-refresh").onclick = load;
  $("me-ctx").onchange = load;
  $("me-shared").onchange = load;
  $("me-q").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  window.addEventListener("mnemos:memoria", () => { if (!loaded) load(); });
  if (location.hash.startsWith("#memoria")) load();
})();
