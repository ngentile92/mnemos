"use strict";
// Memory tab: browse notes per context, read one note with provenance + history, correct / pin / mark obsolete /
// forget / undo, and "Something isn't right?" proposals shown as diffs. Edits go through the gateway's own tools.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("view-memoria")) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CTXS = (window.MNEMOS?.contexts || []).map((c) => c.name);
  let items = [], loaded = false, selId = null, poll = null;
  $("me-ctx").innerHTML = CTXS.map((c) => `<option>${esc(c)}</option>`).join("");
  const saved = localStorage.getItem("mnemos.memctx");
  if (saved && CTXS.includes(saved)) $("me-ctx").value = saved;
  const ctx = () => $("me-ctx").value;
  const when = (s) => {
    if (!s) return "";
    const t = String(s);
    if (/[zZ]|[+-]\d\d:\d\d$/.test(t)) { const d = new Date(t); if (!isNaN(d)) return d.toLocaleString([], { dateStyle: "short", timeStyle: "short" }); }
    return t.replace("T", " ").slice(0, 16);
  };

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
  let toastT = null;
  function toast(msg, undoId) {
    const t = $("me-toast");
    t.innerHTML = `<span>${esc(msg)}</span>` + (undoId ? '<button id="me-toast-undo">Undo</button>' : "");
    t.classList.remove("hidden");
    if (undoId) $("me-toast-undo").onclick = () => { t.classList.add("hidden"); doUndo(undoId); };
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 7000);
  }
  const badges = (it) => [
    it.pinned && '<span class="badge pin">📌 pinned</span>', it.obsolete && '<span class="badge obs">obsolete</span>',
    it.sync === "processing" && '<span class="badge proc"><span class="spin"></span>processing</span>',
    it.sync === "error" && '<span class="badge obs">sync error</span>', !it.editable && '<span class="badge ro">shared · read-only</span>',
  ].filter(Boolean).join("");

  async function load(keepSel = true) {
    loaded = true;
    if (!items.length) $("me-list").innerHTML = '<p class="muted"><span class="spin"></span>loading…</p>';
    try {
      const j = await get("/api/memory/list", { context: ctx(), contains: $("me-q").value, include_shared: $("me-shared").checked ? "1" : "0" });
      items = j.items || [];
      $("me-count").textContent = `${items.length}${j.total > items.length ? " of " + j.total : ""}`;
      renderList();
      const cur = keepSel && items.find((i) => i.id === selId);
      if (cur && !$("me-text")) show(cur, false);
      clearInterval(poll);
      if (items.some((i) => i.sync === "processing")) poll = setInterval(() => load(), 4000);
    } catch (e) { $("me-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  function renderList() {
    $("me-list").innerHTML = items.length ? items.map((it, i) =>
      `<div class="me-item${it.id === selId ? " sel" : ""}" data-i="${i}"><div class="m"><span>${esc(when(it.created_at))}</span><span>${esc(it.source_app || "")}</span>${badges(it)}</div>` +
      `<div class="t">${esc(it.text)}</div></div>`).join("") : '<p class="muted">No notes.</p>';
  }

  async function show(it, scroll = true) {
    selId = it.id; renderList();
    const d = $("me-detail");
    const p = it.provenance || {};
    d.innerHTML = `<div class="card-h"><h2>Note</h2><div>${badges(it)}</div></div>
      <div id="me-body"><div class="me-note">${esc(it.text)}</div></div>
      ${it.editable ? `<div class="me-actions" id="me-actions">
        <button class="mbtn primary" id="me-edit">Correct</button>
        <button class="mbtn" id="me-pin">${it.pinned ? "Unpin" : "Pin"}</button>
        <button class="mbtn" id="me-obs">${it.obsolete ? "Not obsolete" : "Mark obsolete"}</button>
        <button class="mbtn" id="me-undo">Undo last change</button>
        <button class="mbtn danger" id="me-forget">Forget</button></div>`
        : '<p class="muted small">Shared notes are read-only here (owner edits them with scripts/memory_admin.py).</p>'}
      <dl class="me-meta"><dt>Dataset</dt><dd>${esc(it.dataset)}</dd>
        <dt>Saved</dt><dd>${esc(when(p.saved_at || it.created_at))} · ${esc(p.source_app || it.source_app || "unknown app")}</dd>
        ${p.updated_at ? `<dt>Last change</dt><dd>${esc(when(p.updated_at))} · ${esc(p.updated_by || "")}</dd>` : ""}
        ${p.obsolete ? `<dt>Obsolete</dt><dd>${esc(when(p.obsolete.at))} ${esc(p.obsolete.reason || "")}</dd>` : ""}
        <dt>id</dt><dd class="small muted">${esc(it.id)}</dd></dl>
      <h2 style="margin-top:14px">History</h2><div id="me-hist" class="me-hist small muted"><span class="spin"></span></div>`;
    if (scroll && window.innerWidth < 900) d.scrollIntoView({ behavior: "smooth" });
    if (it.editable) {
      $("me-edit").onclick = () => editMode(it);
      $("me-pin").onclick = (e) => act(e.target, "/api/memory/pin", { id: it.id, pinned: !it.pinned }, it.pinned ? "Unpinned" : "Pinned ✓ — shows first in searches");
      $("me-obs").onclick = (e) => {
        const reason = it.obsolete ? "" : prompt("Why is it no longer true? (optional)");
        if (reason === null) return;
        act(e.target, "/api/memory/obsolete", { id: it.id, obsolete: !it.obsolete, reason }, it.obsolete ? "Back in searches" : "Marked obsolete ✓ — kept, but not used in answers");
      };
      $("me-undo").onclick = () => doUndo(it.id);
      $("me-forget").onclick = (e) => confirm("Forget this note? You can undo it.") && act(e.target, "/api/memory/delete", { id: it.id }, "Forgotten", it.id);
    }
    history(it.id);
  }
  async function history(id) {
    try {
      const h = await get("/api/memory/history", { context: ctx(), id });
      const v = h.previous_versions || [];
      $("me-hist").innerHTML = (h.provenance?.source ? `<p>${esc(h.provenance.source)}</p>` : "") + (v.length ? v.map((x) =>
        `<details><summary>${esc(when(x.replaced_at))} · ${esc(x.action)} · ${esc(x.by || "")}</summary><pre>${esc(x.text)}</pre></details>`).join("") : "<p>No previous versions.</p>");
    } catch (e) { $("me-hist").textContent = e.message; }
  }
  function editMode(it) {
    $("me-body").innerHTML = `<textarea id="me-text" maxlength="20000">${esc(it.text)}</textarea>`;
    $("me-actions").innerHTML = '<button class="mbtn primary" id="me-save">Save correction</button><button class="mbtn" id="me-cancel">Cancel</button>' +
      '<span class="muted small">Saved instantly as a new version; the knowledge graph re-processes it in the background (≈20–60 s).</span>';
    $("me-text").focus();
    $("me-cancel").onclick = () => show(it);
    $("me-save").onclick = async (e) => {
      const text = $("me-text").value;
      if (text.trim() === it.text.trim()) return show(it);
      e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span>Saving…';
      try {
        const j = await post("/api/memory/update", { context: ctx(), id: it.id, text });
        Object.assign(it, { text, sync: j.status === "processing" ? "processing" : undefined });
        toast(`Saved ✓${j.version ? ` (version ${j.version})` : ""}`, it.id);
        show(it, false); load();
      } catch (err) { e.target.disabled = false; e.target.textContent = "Save correction"; toast("Not saved: " + err.message); }
    };
  }
  async function act(btn, path, body, ok, undoId) {
    btn.disabled = true; const label = btn.textContent; btn.innerHTML = `<span class="spin"></span>${esc(label)}`;
    try { await post(path, { context: ctx(), ...body }); toast(ok, undoId); await load(); const cur = items.find((i) => i.id === selId); if (cur) show(cur, false); else $("me-detail").innerHTML = '<p class="muted">Pick a note on the left.</p>'; }
    catch (e) { btn.disabled = false; btn.textContent = label; toast("Error: " + e.message); }
  }
  async function doUndo(id) {
    try { const j = await post("/api/memory/undo", { context: ctx(), id }); toast(j.action === "restored" ? "Restored ✓" : "Undone ✓ — previous version is back"); await load(); const cur = items.find((i) => i.id === (j.id || id)); if (cur) show(cur, false); }
    catch (e) { toast("Undo failed: " + e.message); }
  }

  // ------------------------------------------------ "Something isn't right?"
  const diffHtml = (d) => (d || []).map((x) => x.op === "del" ? `<del>${esc(x.text)}</del>` : x.op === "add" ? `<ins>${esc(x.text)}</ins>` : esc(x.text)).join(" ");
  function saveCard(q, hasProps) {
    return `<div class="me-prop me-save"><div class="small muted">${hasProps ? "Or keep it as a separate fact" : "Nothing in memory says otherwise"}</div>
      <div class="why">${hasProps ? "Save it as a new note instead of correcting." : "Save it as a new note?"}</div>
      <div class="me-row"><select id="me-save-ctx" aria-label="Context">${CTXS.map((c) => `<option${c === ctx() ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <input id="me-save-proj" placeholder="project (only for contexts with projects)" maxlength="80"></div>
      <textarea id="me-save-text" style="min-height:70px;margin-top:8px">${esc(q)}</textarea>
      <div class="me-actions"><button class="mbtn ${hasProps ? "" : "primary"}" id="me-save-new">Save as new note</button>
      <span class="muted small">Saved with origin “dashboard”; you can edit or forget it later.</span></div></div>`;
  }
  $("me-proposals").addEventListener("click", async (ev) => {
    if (ev.target.id !== "me-save-new") return;
    const b = ev.target, c = $("me-save-ctx").value;
    b.disabled = true; b.innerHTML = '<span class="spin"></span>Saving…';
    try {
      await post("/api/memory/save", { context: c, text: $("me-save-text").value, project: $("me-save-proj").value });
      b.closest(".me-save").innerHTML = `<span class="small">Saved as a new note in <b>${esc(c)}</b> ✓ (it appears in the list in a few seconds)</span>`;
      toast("Saved as new note ✓");
      if (c === ctx()) setTimeout(() => load(), 2500);
    } catch (e) { b.disabled = false; b.textContent = "Save as new note"; toast("Not saved: " + e.message); }
  });
  $("me-dispute-go").onclick = async () => {
    const box = $("me-proposals"), btn = $("me-dispute-go"), q = $("me-dispute").value.trim();
    if (q.length < 5) return toast("Describe what changed (a short sentence).");
    btn.disabled = true;
    box.innerHTML = '<p class="muted"><span class="spin"></span>Checking whether a note says otherwise… (local model, usually under 5 s)</p>';
    try {
      const j = await post("/api/memory/dispute", { context: ctx(), correction: q });
      const ps = j.proposals || [];
      box.innerHTML = ps.length ? ps.map((p, i) => `<div class="me-prop"><div class="small muted">${i === 0 ? "Best match · " : ""}${esc(p.dataset)} · ${p.action === "obsolete" ? "mark as obsolete" : "correct"}</div>
        <div class="why">${esc(p.why)}</div>
        ${p.action === "update" ? `<div class="diff">${diffHtml(p.diff)}</div>` : `<div class="diff"><del>${esc(p.text)}</del></div>`}
        <div class="me-actions"><button class="mbtn primary" data-apply="${i}">${p.action === "update" ? "Apply correction" : "Mark obsolete"}</button><button class="mbtn" data-skip="${i}">Dismiss</button></div></div>`).join("")
        : "";
      box.insertAdjacentHTML("beforeend", saveCard(q, ps.length > 0));
      const ck = j.checked || [];
      if (ck.length) box.insertAdjacentHTML("beforeend", `<details class="me-checked"><summary class="small muted">Checked ${ck.length} note${ck.length > 1 ? "s" : ""}${j.cached ? " · same answer as before (cached)" : ""}${j.timings_ms ? ` · ${((j.timings_ms.retrieval + j.timings_ms.model) / 1000).toFixed(1)} s` : ""} — why</summary>` +
        ck.map((c) => `<div class="small"><span class="${c.verdict === "proposed" ? "ok" : "muted"}">${esc(c.verdict)}</span> — ${esc(c.preview)}…</div>`).join("") + "</details>");
      box.querySelectorAll("[data-skip]").forEach((b) => (b.onclick = () => b.closest(".me-prop").remove()));
      box.querySelectorAll("[data-apply]").forEach((b) => (b.onclick = async () => {
        const p = ps[+b.dataset.apply];
        b.disabled = true; b.innerHTML = '<span class="spin"></span>Applying…';
        try {
          const r = p.action === "update" ? await post("/api/memory/update", { context: ctx(), id: p.id, text: p.proposed_text })
            : await post("/api/memory/obsolete", { context: ctx(), id: p.id, obsolete: true, reason: q.slice(0, 300) });
          b.closest(".me-prop").innerHTML = `<span class="small">Applied ✓${r.version ? ` (version ${r.version})` : ""}</span>`;
          toast("Applied ✓", p.action === "update" ? p.id : null); load();
        } catch (e) { b.disabled = false; b.textContent = "Apply"; toast("Error: " + e.message); }
      }));
    } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    btn.disabled = false;
  };

  $("me-list").addEventListener("click", (ev) => { const n = ev.target.closest(".me-item"); if (n) show(items[+n.dataset.i]); });
  $("me-refresh").onclick = () => load();
  $("me-ctx").onchange = () => { localStorage.setItem("mnemos.memctx", ctx()); selId = null; items = []; $("me-detail").innerHTML = '<p class="muted">Pick a note on the left.</p>'; load(); };
  $("me-shared").onchange = () => load();
  $("me-q").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  $("me-dispute").addEventListener("keydown", (e) => { if (e.key === "Enter") $("me-dispute-go").click(); });
  window.addEventListener("mnemos:memoria", () => { if (!loaded) load(); });
  if (location.hash.startsWith("#memoria")) load();
})();
