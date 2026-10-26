"use strict";
// Memory tab: browse notes per context, read one note with provenance + history, correct / pin / mark obsolete /
// forget / undo, and "Something isn't right?" proposals shown as diffs. Edits go through the gateway's own tools.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("view-memoria")) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CTXS = (window.MNEMOS?.contexts || []).map((c) => c.name);
  let items = [], loaded = false, selId = null, poll = null;
  // 0.17 redesign: filters (left), dense list with multi-select (middle), detail (right)
  let shared = localStorage.getItem("mnemos.memshared") !== "0";
  const F = { show: "all", app: "", origin: "", when: "" };
  const picked = new Set();
  const DAY = 864e5;
  const ageDays = (it) => { const d = Date.parse(it.created_at); return isNaN(d) ? 1e9 : (Date.now() - d) / DAY; };
  const isImported = (it) => (it.tags || []).some((t) => String(t).startsWith("import")) || /^import/.test(it.source_app || "");
  const appName = (it) => it.source_app || "unknown app";
  const TESTS = {
    show: { all: () => true, pinned: (it) => it.pinned, mine: (it) => it.editable, shared: (it) => !it.editable,
      old: (it) => it.obsolete || it.validity === "expired" },
    origin: { "": () => true, imported: isImported, saved: (it) => !isImported(it) },
    when: { "": () => true, d7: (it) => ageDays(it) <= 7, d30: (it) => ageDays(it) <= 30, older: (it) => ageDays(it) > 30 },
  };
  const passes = (it, skip) => (skip === "show" || TESTS.show[F.show](it)) && (skip === "app" || !F.app || appName(it) === F.app)
    && (skip === "origin" || TESTS.origin[F.origin](it)) && (skip === "when" || TESTS.when[F.when](it));
  // the hub dashboard shares this file with an older layout: every 0.17 element is optional
  const on = (id, prop, fn) => { const el = $(id); if (el) el[prop] = fn; };
  if ($("me-shared") && !$("me-filters")) { $("me-shared").checked = shared; $("me-shared").onchange = (e) => { shared = e.target.checked; localStorage.setItem("mnemos.memshared", shared ? "1" : "0"); load(); }; }
  function renderFilters() {
    if (!$("me-filters")) return;
    const n = (key, test) => items.filter((it) => passes(it, key) && test(it)).length;
    const opt = (key, val, label, cnt) => `<button class="me-f${F[key] === val ? " on" : ""}" data-fk="${key}" data-fv="${esc(val)}"><span>${esc(label)}</span><span class="me-fn">${cnt}</span></button>`;
    const apps = [...new Set(items.map(appName))].sort();
    $("me-filters").innerHTML = `<h3>Show</h3>${opt("show", "all", "All notes", n("show", TESTS.show.all))}${opt("show", "pinned", "Pinned", n("show", TESTS.show.pinned))}
      ${opt("show", "mine", "Only this space", n("show", TESTS.show.mine))}${shared ? opt("show", "shared", "Shared (read-only)", n("show", TESTS.show.shared)) : ""}${opt("show", "old", "Obsolete or expired", n("show", TESTS.show.old))}
      <label class="me-fshared small"><input type="checkbox" id="me-shared" ${shared ? "checked" : ""}> Include shared notes</label>
      <h3>App</h3>${opt("app", "", "Any app", n("app", () => true))}${apps.map((a) => opt("app", a, a, n("app", (it) => appName(it) === a))).join("")}
      <h3>Origin</h3>${opt("origin", "", "Any", n("origin", () => true))}${opt("origin", "imported", "Imported", n("origin", isImported))}${opt("origin", "saved", "Saved by an AI or you", n("origin", (it) => !isImported(it)))}
      <h3>When</h3>${opt("when", "", "Any time", n("when", () => true))}${opt("when", "d7", "Last 7 days", n("when", TESTS.when.d7))}${opt("when", "d30", "Last 30 days", n("when", TESTS.when.d30))}${opt("when", "older", "Older", n("when", TESTS.when.older))}
      ${F.show !== "all" || F.app || F.origin || F.when ? '<button class="mbtn sm me-freset" id="me-freset">Clear filters</button>' : ""}`;
  }
  function renderBulk() {
    if (!$("me-bulk")) return;
    $("me-bulk").classList.toggle("hidden", !picked.size);
    $("me-bulk-n").textContent = `${picked.size} selected`;
    $("me-b-to").innerHTML = spOpts(CTXS.filter((c) => c !== ctx()));
    $("me-b-move").classList.toggle("hidden", CTXS.length < 2); $("me-b-to").classList.toggle("hidden", CTXS.length < 2);
  }
  $("me-ctx").innerHTML = CTXS.map((c) => `<option>${esc(c)}</option>`).join("");
  const saved = localStorage.getItem("mnemos.memctx");
  if (saved && CTXS.includes(saved)) $("me-ctx").value = saved;
  const ctx = () => $("me-ctx").value;
  // spaces by their display name (Spaces screen), translated; the key stays the value
  const spLabel = (name) => { const s = (((window.MNEMOS_SPACES || {}).spaces) || []).find((x) => x.name === name); const l = (s && s.label) || name; return window.tr ? window.tr(l) : l; };
  const spOpts = (names, sel) => names.map((c) => `<option value="${esc(c)}"${c === sel ? " selected" : ""}>${esc(spLabel(c))}</option>`).join("");
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
    it.validity === "expired" && `<span class="badge obs" title="no longer true after ${esc(it.provenance?.valid_until || "")}">expired</span>`,
    it.validity === "future" && `<span class="badge proc" title="true from ${esc(it.provenance?.valid_from || "")}">not yet</span>`,
    it.sync === "processing" && '<span class="badge proc"><span class="spin"></span>processing</span>',
    it.sync === "error" && '<span class="badge obs">sync error</span>', !it.editable && '<span class="badge ro">shared · read-only</span>',
  ].filter(Boolean).join("");

  async function load(keepSel = true) {
    loaded = true;
    if (!items.length) $("me-list").innerHTML = '<p class="muted"><span class="spin"></span>loading…</p>';
    try {
      const j = await get("/api/memory/list", { context: ctx(), include_shared: shared ? "1" : "0" });
      items = j.items || [];
      renderList();
      const cur = keepSel && items.find((i) => i.id === selId);
      if (cur && !$("me-text")) show(cur, false);
      else if (!cur && window.innerWidth >= 900) { const first = items.find((i) => passes(i)); if (first) show(first, false); }
      clearInterval(poll);
      if (items.some((i) => i.sync === "processing")) poll = setInterval(() => load(), 4000);
    } catch (e) { $("me-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  let related = [];
  // short queries ("PR", "AI") match whole words only; longer ones match anywhere
  const rx = (q) => {
    const e = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return q.length <= 3 ? new RegExp(`(?<![\\p{L}\\p{N}])${e}s?(?![\\p{L}\\p{N}])`, "giu") : new RegExp(e, "giu");
  };
  const matches = (text, q) => rx(q).test(String(text));
  const hl = (text, q) => {
    if (!q) return esc(text);
    const t = String(text), r = rx(q);
    let out = "", last = 0, m, first = true;
    while ((m = r.exec(t))) {
      if (first && m.index > 80) { out = "…"; last = m.index - 60; }
      first = false;
      out += esc(t.slice(last, m.index)) + "<mark>" + esc(m[0]) + "</mark>"; last = m.index + m[0].length;
    }
    return out + esc(t.slice(last));
  };
  const short = (s) => { const d = new Date(s); return isNaN(d) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short" }); };
  const row = (it, q, extra = "") => `<div class="me-item me-row1${it.id === selId ? " sel" : ""}${picked.has(it.id) ? " picked" : ""}" data-id="${esc(it.id)}">` +
      `${it.editable ? `<input type="checkbox" class="me-pick" data-pick="${esc(it.id)}" ${picked.has(it.id) ? "checked" : ""} aria-label="Select">` : '<span class="me-pick-sp"></span>'}` +
      `<div class="t">${it.pinned ? "📌 " : ""}${hl(it.text, q)}</div><div class="m">${badges(it).replace('<span class="badge pin">📌 pinned</span>', "").replace("shared · read-only</span>", "shared</span>")}${extra}<span class="me-app">${esc(appName(it))}</span><span class="me-date">${esc(short(it.created_at))}</span></div></div>`;
  function renderList() {
    const q = $("me-q").value.trim();
    renderFilters(); renderBulk();
    const pool = items.filter((it) => passes(it));
    const hits = q ? pool.filter((it) => matches(it.text, q)) : pool;
    $("me-count").textContent = q ? `${hits.length} of ${pool.length} match “${q}”` : pool.length === items.length ? `${items.length}` : `${pool.length} of ${items.length}`;
    const hitIds = new Set(hits.map((h) => h.id));
    const rel = q ? related.filter((r) => !hitIds.has(r.id)).map((r) => items.find((i) => i.id === r.id)).filter(Boolean) : [];
    $("me-list").innerHTML = (hits.length ? hits.map((it) => row(it, q)).join("") : `<div class="empty"><p class="muted">${q ? "No note contains that text." : items.length ? "No note matches these filters." : "No notes yet."}</p></div>`) +
      (rel.length ? `<div class="me-relh small muted">Related by meaning</div>` + rel.map((it) => row(it, "", '<span class="badge">related</span>')).join("") : "") +
      (q && relPending ? '<p class="small muted"><span class="spin"></span>looking for related notes…</p>' : "");
  }
  let relT = null, relPending = false;
  function onFilter() {
    renderList();
    clearTimeout(relT);
    const q = $("me-q").value.trim();
    related = [];
    if (q.length < 2) return;
    relPending = true; renderList();
    relT = setTimeout(async () => {
      try { related = ((await get("/api/memory/related", { context: ctx(), q, include_shared: shared ? "1" : "0" })).results || []).filter((r) => r.id); }
      catch { related = []; }
      relPending = false;
      if ($("me-q").value.trim() === q) renderList();
    }, 500);
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
        <button class="mbtn" id="me-move">Move…</button>
        <button class="mbtn" id="me-promote">Promote to shared…</button>
        <button class="mbtn danger" id="me-forget">Forget</button></div>`
        : '<p class="muted small">Shared notes are read-only here.</p>'}
      <dl class="me-meta"><dt>Space</dt><dd>${esc(spLabel(it.dataset))}</dd>
        <dt>Saved</dt><dd>${esc(when(p.saved_at || it.created_at))} · ${esc(p.source_app || it.source_app || "unknown app")}</dd>
        ${p.updated_at ? `<dt>Last change</dt><dd>${esc(when(p.updated_at))} · ${esc(p.updated_by || "")}</dd>` : ""}
        <dt>True</dt><dd id="me-valid">${validText(p)} ${it.editable ? '<button class="mbtn sm" id="me-valid-edit">Set dates…</button>' : ""}</dd>
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
      $("me-move").onclick = () => moveMode(it);
      $("me-valid-edit").onclick = () => validMode(it);
      $("me-promote").onclick = () => promoteMode(it);
      $("me-forget").onclick = (e) => confirm("Forget this note? You can undo it.") && act(e.target, "/api/memory/delete", { id: it.id }, "Forgotten", it.id);
    }
    history(it.id);
  }
  async function history(id) {
    try {
      const h = await get("/api/memory/history", { context: ctx(), id });
      const v = h.previous_versions || [];
      const src = h.provenance?.source && !/metadata|cognee/i.test(h.provenance.source) ? h.provenance.source : "";
      $("me-hist").innerHTML = (src ? `<p>${esc(src)}</p>` : "") + (v.length ? v.map((x) =>
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
  function confirmPanel(it, html, label, run) {
    $("me-actions").innerHTML = `<div class="me-confirm">${html}
      <label class="small"><input type="checkbox" id="me-ok"> I understand</label>
      <div class="me-actions"><button class="mbtn primary" id="me-go" disabled>${esc(label)}</button><button class="mbtn" id="me-cancel">Cancel</button></div></div>`;
    $("me-ok").onchange = () => { $("me-go").disabled = !$("me-ok").checked; };
    $("me-cancel").onclick = () => show(it);
    $("me-go").onclick = async (e) => {
      e.target.disabled = true; e.target.innerHTML = `<span class="spin"></span>${esc(label)}…`;
      try { await run(); } catch (err) { toast("Not done: " + err.message); show(it, false); }
    };
  }
  function validText(p) {
    const f = p.valid_from, u = p.valid_until;
    if (!f && !u) return '<span class="muted">no dates (always)</span>';
    const today = new Date().toISOString().slice(0, 10);
    const st = u && u < today ? ' <span class="badge obs">expired</span>' : f && f > today ? ' <span class="badge proc">not yet</span>' : ' <span class="badge ok">current</span>';
    return `${f ? "since " + esc(f) : "until"}${f && u ? " · until " : " "}${u ? esc(u) : ""}${st}`;
  }
  function validMode(it) {
    const p = it.provenance || {};
    $("me-valid").innerHTML = `<div class="me-row"><label class="small">since <input type="date" id="me-vf" value="${esc(p.valid_from || "")}"></label>
      <label class="small">until <input type="date" id="me-vu" value="${esc(p.valid_until || "")}"></label>
      <button class="mbtn primary sm" id="me-vsave">Save</button><button class="mbtn sm" id="me-vcancel">Cancel</button></div>
      <div class="small muted">Leave “until” empty while it is still true. Expired facts stay saved but go last in searches and answers treat them as past.</div>`;
    $("me-vcancel").onclick = () => show(it, false);
    $("me-vsave").onclick = (e) => act(e.target, "/api/memory/validity", { id: it.id, valid_from: $("me-vf").value, valid_until: $("me-vu").value }, "Dates saved ✓");
  }
  function moveMode(it) {
    const others = CTXS.filter((c) => c !== ctx());
    confirmPanel(it, `<p><b>Move this note to another context.</b> It is copied there (tagged “moved-from-${esc(ctx())}”, origin dashboard) and then forgotten here — you can undo the forget in <b>${esc(ctx())}</b>.</p>
      <div class="me-row"><select id="me-move-to">${spOpts(others)}</select>
      <input id="me-move-proj" placeholder="project (only for contexts with projects)" maxlength="80"></div>`, "Move note", async () => {
      const to = $("me-move-to").value;
      await post("/api/memory/move", { context: ctx(), id: it.id, to, text: it.text, project: $("me-move-proj").value, confirm: true });
      toast(`Moved to ${spLabel(to)} ✓`, it.id); selId = null; $("me-detail").innerHTML = '<p class="muted">Pick a note on the left.</p>'; load();
    });
  }
  function promoteMode(it) {
    confirmPanel(it, `<p><b>Promote to shared.</b> A copy goes to <b>shared</b>, which <b>every context</b> (and every connected assistant) can read. The original stays here. Shared notes can't be edited from the dashboard — check nothing in it is private to ${esc(ctx())}.</p>
      <div class="me-note small">${esc(it.text)}</div>`, "Promote to shared", async () => {
      await post("/api/memory/promote", { context: ctx(), id: it.id, confirm: true });
      toast("Promoted to shared ✓"); show(it, false); load();
    });
  }

  // ------------------------------------------------ memory health (proposals from the checks; the user decides)
  let inbox = [], hyFilter = "", lastBatch = null, hyPoll = null;
  // after a health change: refresh the notes list and tell the files view (files mode) to reload too
  const changed = () => { load(); window.dispatchEvent(new Event("mnemos:memory-changed")); };
  const GROUPS = [
    ["junk", "Not about you", "Code, tables, logs and task steps that slipped in."],
    ["rewrite", "Rewrite", "Written about you in the third person."],
    ["merge", "Duplicates", "The same fact saved twice."],
    ["contradiction", "Disagree", "Two notes that cannot both be true."],
    ["move", "Other space", "Notes that look like they belong in another space."],
    ["wrong", "Probably wrong", "About the assistant, a character or someone else."],
    ["stale", "Out of date", "Old notes nobody touched in a year."]];
  const TYPE_LABEL = Object.fromEntries(GROUPS.map((g) => [g[0], g[1]]));
  async function loadInbox() {
    let j;
    try { j = await get("/api/hygiene", {}); } catch { return; }
    inbox = j.items || []; lastBatch = j.last_batch || null;
    const counts = j.counts || {};
    $("me-inbox-head").textContent = j.running ? "checking…" : j.date ? `checked ${when(j.at || j.date)} · ${inbox.length} to review${j.total > inbox.length ? ` · ${j.total - inbox.length} done` : ""}` : "not checked yet";
    window.MNEMOS_HY_N = inbox.length;
    if ($("me-sugg")) { const n = inbox.length + (window.MNEMOS_RL_N || 0); $("me-sugg-n").textContent = n ? String(n) : ""; $("me-sugg").classList.toggle("has", n > 0); }
    $("me-hy-undo").classList.toggle("hidden", !lastBatch);
    if (lastBatch) $("me-hy-undo").textContent = `Undo last cleanup (${lastBatch.count})`;
    $("me-hy-groups").innerHTML = GROUPS.filter((g) => counts[g[0]]).map((g) => `<div class="me-hy-g${hyFilter === g[0] ? " on" : ""}" data-g="${g[0]}">
      <b>${counts[g[0]]}</b> ${esc(g[1])}<div class="small muted">${esc(g[2])}</div>
      <div class="me-actions"><button class="mbtn" data-gs="${g[0]}">${hyFilter === g[0] ? "Show all" : "Review"}</button>${g[0] === "contradiction" ? "" : `<button class="mbtn primary" data-ga="${g[0]}">Accept all ${counts[g[0]]}</button>`}</div></div>`).join("");
    const shown = inbox.map((i, n) => [i, n]).filter(([i]) => !hyFilter || i.type === hyFilter);
    $("me-inbox").innerHTML = shown.length ? shown.map(([i, n]) => `<div class="me-prop" data-k="${esc(i.key)}">
      <div class="small muted">${esc(TYPE_LABEL[i.type] || i.type)} · ${esc(i.context)}${i.to ? " → " + esc(i.to) : ""}</div><div class="why">${esc(i.title)}</div>
      <p class="small">${esc(i.explain)}</p>
      ${i.notes.map((x, k) => `<div class="me-inote"><span class="small muted">${i.notes.length > 1 ? (k ? (i.type === "merge" ? "older copy" : "newer") : (i.type === "merge" ? "kept" : "older")) + " · " : ""}${esc(when(x.created_at))}</span><div>${esc(x.text)}</div></div>`).join("")}
      ${i.proposed_text ? `<div class="me-inote ok"><span class="small muted">${i.type === "merge" ? "merged text" : "new text"}</span><div>${esc(i.proposed_text)}</div></div>` : ""}
      <div class="me-actions">${i.actions.map((a) => `<button class="mbtn ${a.kind}" data-ia="${a.id}" data-n="${n}">${esc(a.label)}</button>`).join("")}<button class="mbtn" data-ia="dismiss" data-n="${n}">Keep as is</button></div></div>`).join("")
      : `<p class="muted small">${j.running ? "Checking your memory…" : "Nothing to review. ✓"}</p>`;
    clearTimeout(hyPoll);
    if (j.running) hyPoll = setTimeout(loadInbox, 3000);
  }
  $("me-hy-groups").addEventListener("click", async (ev) => {
    const s = ev.target.closest("[data-gs]");
    if (s) { hyFilter = hyFilter === s.dataset.gs ? "" : s.dataset.gs; loadInbox(); return; }
    const b = ev.target.closest("[data-ga]"); if (!b) return;
    const typ = b.dataset.ga, n = inbox.filter((i) => i.type === typ).length;
    if (!confirm(`Apply all ${n} “${TYPE_LABEL[typ]}” suggestions? A backup is saved first and you can undo it.`)) return;
    b.disabled = true; b.innerHTML = '<span class="spin"></span>Applying…';
    try {
      const r = await post("/api/hygiene/act", { type: typ, action: "accept" });
      toast(`Done: ${r.done}${r.failed ? `, ${r.failed} not done` : ""} ✓ — “Undo last cleanup” puts them back`);
      hyFilter = ""; await loadInbox(); changed();
    } catch (e) { toast("Not done: " + e.message); loadInbox(); }
  });
  $("me-hy-run").onclick = async () => {
    try { await post("/api/hygiene/run", {}); toast("Checking your memory…"); setTimeout(loadInbox, 800); } catch (e) { toast("Could not start: " + e.message); }
  };
  $("me-hy-undo").onclick = async () => {
    if (!lastBatch || !confirm(`Undo the last cleanup (${lastBatch.count} changes)?`)) return;
    try { const r = await post("/api/hygiene/undo", { batch: lastBatch.id }); toast(`Undone ✓ (${r.reverted} changes)`); await loadInbox(); changed(); }
    catch (e) { toast("Undo failed: " + e.message); }
  };
  $("me-inbox").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-ia]"); if (!b) return;
    const it = inbox[+b.dataset.n], action = b.dataset.ia, card = b.closest(".me-prop");
    card.querySelectorAll("button").forEach((x) => { x.disabled = true; });
    b.innerHTML = `<span class="spin"></span>${esc(b.textContent)}`;
    try {
      await post("/api/hygiene/act", { context: it.context, key: it.key, action });
      card.classList.add("me-gone");
      setTimeout(() => loadInbox(), 250);
      toast(action === "dismiss" ? "Kept as is" : "Done ✓ — “Undo last cleanup” puts it back");
      if (action !== "dismiss") changed();
    } catch (e) { card.querySelectorAll("button").forEach((x) => { x.disabled = false; }); toast("Not done: " + e.message); loadInbox(); }
  });

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
      const sv = await post("/api/memory/save", { context: c, text: $("me-save-text").value, project: $("me-save-proj").value });
      if (sv.status === "duplicate") { b.disabled = false; b.textContent = "Save as new note"; toast("Already saved: a near-identical note exists"); return; }
      if (sv.similar_notes && sv.similar_notes.length) toast("Saved ✓ — similar notes exist; check they don't contradict it");
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
      if (!ck.length && !ps.length) box.insertAdjacentHTML("beforeend", '<p class="small muted">No note in this space mentions it.</p>');
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

  let lastPick = null;
  $("me-list").addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-pick]");
    if (c) {
      const ids = [...$("me-list").querySelectorAll("[data-pick]")].map((x) => x.dataset.pick);
      const on = c.checked;
      if (ev.shiftKey && lastPick && ids.includes(lastPick)) { const [a, b] = [ids.indexOf(lastPick), ids.indexOf(c.dataset.pick)].sort((x, y) => x - y); ids.slice(a, b + 1).forEach((id) => (on ? picked.add(id) : picked.delete(id))); }
      else on ? picked.add(c.dataset.pick) : picked.delete(c.dataset.pick);
      lastPick = c.dataset.pick; renderList(); return;
    }
    const n = ev.target.closest(".me-item"); if (n) show(items.find((i) => i.id === n.dataset.id));
  });
  $("me-filters")?.addEventListener("click", (ev) => {
    if (ev.target.id === "me-freset") { Object.assign(F, { show: "all", app: "", origin: "", when: "" }); return renderList(); }
    const b = ev.target.closest("[data-fk]"); if (!b) return;
    F[b.dataset.fk] = F[b.dataset.fk] === b.dataset.fv && b.dataset.fk !== "show" ? "" : b.dataset.fv; renderList();
  });
  $("me-filters")?.addEventListener("change", (ev) => {
    if (ev.target.id !== "me-shared") return;
    shared = ev.target.checked; localStorage.setItem("mnemos.memshared", shared ? "1" : "0");
    if (!shared && F.show === "shared") F.show = "all"; load();
  });
  async function bulk(btn, label, fn, done) {
    const ids = [...picked].filter((id) => items.some((i) => i.id === id && i.editable));
    if (!ids.length) return;
    btn.disabled = true; const t = btn.textContent; btn.innerHTML = `<span class="spin"></span>${esc(t)}`;
    let ok = 0;
    for (const id of ids) { try { await fn(items.find((i) => i.id === id)); ok++; } catch { /* counted below */ } }
    btn.disabled = false; btn.textContent = t; picked.clear();
    toast(`${done} ${ok}${ok < ids.length ? ` · ${ids.length - ok} not done` : ""} ✓`);
    await load(); changedFiles();
  }
  const changedFiles = () => window.dispatchEvent(new Event("mnemos:memory-changed"));
  on("me-b-pin", "onclick", (e) => bulk(e.target, "Pin", (it) => post("/api/memory/pin", { context: ctx(), id: it.id, pinned: true }), "Pinned:"));
  on("me-b-move", "onclick", (e) => { const to = $("me-b-to").value;
    if (confirm(`Move ${picked.size} notes to ${spLabel(to)}? Each one is copied there and forgotten here; you can undo each forget.`))
      bulk(e.target, "Move", (it) => post("/api/memory/move", { context: ctx(), id: it.id, to, text: it.text, project: "", confirm: true }), `Moved to ${spLabel(to)}:`); });
  on("me-b-forget", "onclick", (e) => { if (confirm(`Forget ${picked.size} notes? Each one can be restored from its history.`))
      bulk(e.target, "Forget", (it) => post("/api/memory/delete", { context: ctx(), id: it.id }), "Forgotten:"); });
  on("me-b-clear", "onclick", () => { picked.clear(); renderList(); });
  on("me-new", "onclick", () => { const c = $("me-new-card"); c.classList.toggle("hidden"); if (!c.classList.contains("hidden")) $("me-dispute").focus(); });
  on("me-new-close", "onclick", () => $("me-new-card").classList.add("hidden"));
  // local app (0.19): "Suggestions" opens every decision at once (#me-review); the dashboard only has the health card
  on("me-sugg", "onclick", () => { const box = $("me-review") || $("me-inbox-card"); box.classList.toggle("hidden"); $("me-sugg").classList.toggle("on", !box.classList.contains("hidden")); });
  if ($("me-kbd")) $("me-kbd").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (!location.hash.startsWith("#memoria")) location.hash = "memoria";
      setTimeout(() => { $("me-q").focus(); $("me-q").select(); }, 30);
    } else if (e.key === "Escape" && document.activeElement === $("me-q") && $("me-q").value) { $("me-q").value = ""; onFilter(); }
  });
  $("me-refresh").onclick = () => load();
  $("me-ctx").onchange = () => { localStorage.setItem("mnemos.memctx", ctx()); selId = null; items = []; picked.clear(); $("me-detail").innerHTML = '<p class="muted">Pick a note on the left.</p>'; load(); };
  $("me-q").addEventListener("input", onFilter);
  $("me-dispute").addEventListener("keydown", (e) => { if (e.key === "Enter") $("me-dispute-go").click(); });
  window.addEventListener("mnemos:memoria", () => { if (!loaded) { load(); loadInbox(); } });
  if (location.hash.startsWith("#memoria")) { load(); loadInbox(); }
})();
