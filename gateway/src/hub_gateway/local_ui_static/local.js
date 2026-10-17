"use strict";
// Mnemos local app: unlock (token from Mnemos Desktop or password), then the Notes view (memory.js, shared with
// the dashboard), a lightweight entity graph and a skills editor. Everything talks to this hub on 127.0.0.1.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
    return j;
  }
  window.MNEMOS_API = api;

  async function start() {
    const m = location.hash.match(/^#k=([\w-]+)/);
    if (m) {
      history.replaceState(null, "", location.pathname + "#memoria");
      try { await api("/api/login", { token: m[1] }); } catch { /* fall through to the password form */ }
    }
    let s;
    try { s = await api("/api/session"); } catch { return showLogin(); }
    boot(s);
  }
  function showLogin() {
    $("login").classList.remove("hidden");
    $("login-pw").focus();
    const go = async () => {
      $("login-err").textContent = "";
      try { await api("/api/login", { password: $("login-pw").value }); $("login").classList.add("hidden"); start(); }
      catch (e) { $("login-err").textContent = e.message; }
    };
    $("login-go").onclick = go;
    $("login-pw").onkeydown = (e) => { if (e.key === "Enter") go(); };
  }

  function boot(s) {
    window.MNEMOS = { contexts: s.contexts.map((name) => ({ name })) };
    $("app").classList.remove("hidden"); $("tabs").classList.remove("hidden");
    for (const id of ["g-ctx", "s-ctx", "i-ctx"]) $(id).innerHTML = s.contexts.map((c) => `<option>${esc(c)}</option>`).join("");
    const sc = document.createElement("script");
    sc.src = "/ui/memory.js";
    sc.onload = () => route();
    document.body.appendChild(sc);
    window.addEventListener("hashchange", route);
  }
  const loaded = {};
  function route() {
    const v = (location.hash.slice(1).split(/[?&]/)[0] || "memoria").replace(/^k=.*/, "memoria");
    const view = ["memoria", "graph", "skills", "import"].includes(v) ? v : "memoria";
    document.querySelectorAll(".view").forEach((el) => el.classList.toggle("hidden", el.id !== "view-" + view));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
    if (!loaded[view]) {
      loaded[view] = true;
      if (view === "memoria") window.dispatchEvent(new Event("mnemos:memoria"));
      if (view === "graph") loadGraph();
      if (view === "skills") loadSkills();
    }
  }

  // ------------------------------------------------ graph
  let cy = null, graph = null;
  async function loadGraph() {
    $("g-node").innerHTML = '<p class="muted"><span class="spin"></span>reading your notes…</p>';
    try { graph = await api("/api/graph?" + new URLSearchParams({ context: $("g-ctx").value })); }
    catch (e) { $("g-node").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    if (cy) cy.destroy();
    const max = Math.max(1, ...graph.nodes.map((n) => n.count));
    cy = window.cytoscape({
      container: $("cy"),
      elements: [
        ...graph.nodes.map((n) => ({ data: { id: n.id, label: n.label, size: 14 + 30 * (n.count / max), kind: n.kind } })),
        ...graph.edges.map((e) => ({ data: { id: e.source + "|" + e.target, source: e.source, target: e.target, w: e.weight } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", width: "data(size)", height: "data(size)", "background-color": "#7c5cff", color: "#e7ecf7", "font-size": 11, "text-valign": "bottom", "text-margin-y": 4 } },
        { selector: 'node[kind = "tag"]', style: { "background-color": "#22d3ee" } },
        { selector: "edge", style: { width: "mapData(w, 1, 6, 1, 5)", "line-color": "rgba(148,163,209,.35)", "curve-style": "bezier" } },
        { selector: ".dim", style: { opacity: 0.15 } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#fbbf24" } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: 9000 },
    });
    cy.on("tap", "node", (ev) => showNode(ev.target.id()));
    $("g-node").innerHTML = graph.nodes.length ? `<p class="muted">${graph.nodes.length} names and topics from ${graph.notes} notes. Tap one.</p>`
      : '<p class="muted">No names or topics yet: add a few notes first.</p>';
  }
  function showNode(id) {
    const n = graph.nodes.find((x) => x.id === id); if (!n) return;
    const nb = graph.edges.filter((e) => e.source === id || e.target === id).map((e) => (e.source === id ? e.target : e.source));
    const label = (k) => (graph.nodes.find((x) => x.id === k) || {}).label || k;
    $("g-node").innerHTML = `<h2>${esc(n.label)}</h2><p class="small muted">in ${n.count} note${n.count > 1 ? "s" : ""}${nb.length ? " · with " + nb.slice(0, 8).map((k) => esc(label(k))).join(", ") : ""}</p>` +
      n.notes.map((nid) => { const t = graph.texts[nid] || ""; return `<div class="me-inote"><div>${esc(t.slice(0, 280))}${t.length > 280 ? "…" : ""}</div></div>`; }).join("");
  }
  $("g-ctx").onchange = loadGraph;
  $("g-fit").onclick = () => cy && cy.fit();
  $("g-q").oninput = () => {
    if (!cy) return;
    const q = $("g-q").value.trim().toLowerCase();
    cy.nodes().forEach((n) => n.toggleClass("dim", !!q && !n.data("label").toLowerCase().includes(q)));
  };

  // ------------------------------------------------ skills
  let skills = [];
  async function loadSkills() {
    try { skills = (await api("/api/skills?" + new URLSearchParams({ context: $("s-ctx").value }))).skills; }
    catch (e) { $("s-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    $("s-list").innerHTML = skills.length ? skills.map((s, i) => `<div class="me-item" data-i="${i}"><div class="m"><span>${esc(s.owner)}</span></div><div class="t"><b>${esc(s.name)}</b> — ${esc(s.description)}</div></div>`).join("")
      : '<p class="muted">No skills yet. Create one with “+ New skill”.</p>';
  }
  async function showSkill(s) {
    let text = "";
    try { text = (await api("/api/skill?" + new URLSearchParams({ context: $("s-ctx").value, name: s.name }))).text; }
    catch (e) { $("s-detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    editor(s.name, s.description, text.replace(/^---[\s\S]*?\n---\n?/, ""), s.editable, false);
  }
  function editor(name, desc, body, editable, isNew) {
    $("s-detail").innerHTML = `<div class="card-h"><h2>${isNew ? "New skill" : esc(name)}</h2></div>
      ${isNew ? '<label class="small muted">Name (lowercase, dashes)<input id="s-name" maxlength="64" placeholder="e.g. email-style"></label>' : ""}
      <label class="small muted">When to use it (one line)<input id="s-desc" maxlength="300" value="${esc(desc)}" ${editable ? "" : "disabled"}></label>
      <label class="small muted">Instructions<textarea id="s-body" style="min-height:260px" ${editable ? "" : "disabled"}>${esc(body)}</textarea></label>
      <div class="me-actions">${editable ? '<button class="mbtn primary" id="s-save">Save</button>' : '<span class="muted small">Shared skill: read-only here.</span>'}</div><p id="s-msg" class="small muted"></p>`;
    if (!editable) return;
    $("s-save").onclick = async () => {
      try {
        await api("/api/skills/save", { context: $("s-ctx").value, name: isNew ? $("s-name").value.trim() : name, description: $("s-desc").value, body: $("s-body").value });
        $("s-msg").textContent = "Saved ✓ — your AI apps see it within a few seconds."; loadSkills();
      } catch (e) { $("s-msg").textContent = "Not saved: " + e.message; }
    };
  }
  $("s-list").addEventListener("click", (ev) => { const n = ev.target.closest(".me-item"); if (n) showSkill(skills[+n.dataset.i]); });
  $("s-ctx").onchange = loadSkills;
  $("s-new").onclick = () => editor("", "", "", true, true);

  // ------------------------------------------------ import
  let review = null;
  function showReview(r) {
    review = r;
    $("i-review").classList.remove("hidden");
    $("i-sum").textContent = `${r.new} new · ${r.duplicates} duplicates skipped${r.truncated ? " · only the first 2000 shown" : ""}`;
    $("i-list").innerHTML = r.items.length ? r.items.map((it, i) => `<label class="imp-item${it.duplicate ? " dup" : ""}"><input type="checkbox" data-i="${i}" ${it.duplicate ? "" : "checked"}>
      <span>${esc(it.text)}${it.duplicate ? ` <span class="badge obs">${esc(it.duplicate)}</span>` : ""}</span></label>`).join("")
      : '<p class="muted">Nothing to import found in that input.</p>';
    $("i-review").scrollIntoView({ behavior: "smooth" });
  }
  async function previewJSON(source, extra, btn) {
    btn.disabled = true; const l = btn.textContent; btn.innerHTML = '<span class="spin"></span>Reading…';
    try { showReview(await api("/api/import/preview", { context: $("i-ctx").value, source, ...extra })); }
    catch (e) { alert("Could not read it: " + e.message); }
    btn.disabled = false; btn.textContent = l;
  }
  $("i-gpt-go").onclick = async (e) => {
    const f = $("i-gpt-file").files[0], btn = e.target;
    if (!f) return previewJSON("chatgpt", { text: $("i-gpt-text").value }, btn);
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Reading the export…';
    try {
      const r = await fetch("/api/import/upload?" + new URLSearchParams({ context: $("i-ctx").value }), { method: "POST", headers: { "X-Mnemos-Write": "1" }, body: f });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
      showReview({ ...j, source: "chatgpt" });
    } catch (err) { alert("Could not read it: " + err.message); }
    btn.disabled = false; btn.textContent = "Review";
  };
  $("i-claude-go").onclick = (e) => previewJSON("claude", { text: $("i-claude-text").value }, e.target);
  $("i-md-go").onclick = async (e) => {
    const fs = [...$("i-md").files].filter((f) => /\.(md|markdown|txt)$/i.test(f.name) && f.size < 2e6).slice(0, 5000);
    if (!fs.length) return alert("Pick a folder with .md or .txt notes.");
    const files = await Promise.all(fs.map(async (f) => ({ name: f.webkitRelativePath || f.name, text: await f.text() })));
    previewJSON("markdown", { files }, e.target);
  };
  $("i-all").onclick = () => $("i-list").querySelectorAll("input").forEach((c) => { c.checked = !review.items[+c.dataset.i].duplicate; });
  $("i-none").onclick = () => $("i-list").querySelectorAll("input").forEach((c) => { c.checked = false; });
  $("i-save").onclick = async (e) => {
    const notes = [...$("i-list").querySelectorAll("input:checked")].map((c) => review.items[+c.dataset.i].text);
    if (!notes.length) return;
    const btn = e.target; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Saving ${notes.length}…`;
    try {
      const r = await api("/api/import/commit", { context: $("i-ctx").value, source: review.source || "text", notes });
      $("i-sum").textContent = `Saved ${r.saved} ✓${r.skipped ? ` · ${r.skipped} already there` : ""}${r.failed ? ` · ${r.failed} failed (${r.errors[0]})` : ""}`;
      $("i-list").innerHTML = '<p class="muted">Done. See them in Notes (tag “import”).</p>';
    } catch (err) { alert("Not saved: " + err.message); }
    btn.disabled = false; btn.textContent = "Save selected";
  };

  // ------------------------------------------------ auto-discover (Scan this Mac)
  const CLOUD_PROMPT = "List everything you remember about me, one fact per line. Only facts about me (who I am, work, projects, people, preferences), no commentary.";
  let dSources = [], dCtxs = [], dRes = null, dAuto = false;
  const kb = (n) => (n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  async function dScan() {
    const b = $("d-scan"); b.disabled = true; b.innerHTML = '<span class="spin"></span>Looking…';
    try {
      const r = await api("/api/discover/scan"); dSources = r.sources; dCtxs = r.contexts;
      $("d-sum").textContent = `Found ${dSources.length} source${dSources.length === 1 ? "" : "s"}`;
      $("d-llm").textContent = r.llm ? `Facts are extracted with Ollama (${r.llm}) on this computer.` : "No Ollama found: simple extraction (first-person facts and instruction files).";
      $("d-sources").innerHTML = dSources.length ? dSources.map((s, i) => `<label class="imp-item"><input type="checkbox" data-i="${i}" checked>
        <span style="flex:1"><b>${esc(s.label)}</b> · ${s.count} ${esc(s.unit)} · ${kb(s.bytes)}<br><span class="small muted">${esc(s.where)}</span></span>
        <select data-c="${i}" aria-label="Context">${dCtxs.map((c) => `<option ${c === s.context ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>`).join("")
        : '<p class="muted small">Nothing found on this computer. Use the options below.</p>';
      $("d-go-row").classList.toggle("hidden", !dSources.length);
    } catch (e) { alert("Scan failed: " + e.message); }
    b.disabled = false; b.textContent = "Scan again";
  }
  $("d-scan").onclick = dScan;
  $("d-go").onclick = async () => {
    const chosen = [...$("d-sources").querySelectorAll("input:checked")].map((c) => ({ id: dSources[+c.dataset.i].id, context: $("d-sources").querySelector(`select[data-c="${c.dataset.i}"]`).value }));
    if (!chosen.length) return;
    try { await api("/api/discover/preview", { sources: chosen }); } catch (e) { return alert(e.message); }
    $("d-go").disabled = true; $("d-progress").classList.remove("hidden"); $("d-review").classList.add("hidden"); $("d-done").classList.add("hidden");
    const tick = async () => {
      let j; try { j = await api("/api/discover/status"); } catch { return setTimeout(tick, 1500); }
      $("d-bar").max = j.total || 1; $("d-bar").value = j.done || 0; $("d-phase").textContent = j.phase || "";
      if (j.state === "running") return setTimeout(tick, 1000);
      $("d-go").disabled = false;
      if (j.state === "error") { $("d-progress").classList.add("hidden"); return alert("Could not read the sources: " + j.error); }
      dShow(j.result);
    };
    tick();
  };
  function dShow(r) {
    dRes = r; $("d-progress").classList.add("hidden"); $("d-review").classList.remove("hidden");
    const sec = r.sources.reduce((a, s) => a + s.secrets_dropped, 0);
    $("d-rsum").textContent = `${r.new} new · ${r.duplicates} already known${sec ? ` · ${sec} left out (looked like secrets)` : ""}${r.timed_out ? " · stopped early (time limit)" : ""}`;
    let last = "";
    $("d-list").innerHTML = r.items.length ? r.items.map((it, i) => {
      const head = it.source_label !== last ? `<h3 class="small" style="margin:14px 0 4px">${esc(it.source_label)} → ${esc(it.context)}</h3>` : "";
      last = it.source_label;
      return head + `<label class="imp-item${it.duplicate ? " dup" : ""}"><input type="checkbox" data-i="${i}" ${it.duplicate ? "" : "checked"}>
        <span>${esc(it.text)}${it.duplicate ? ` <span class="badge obs">${esc(it.duplicate)}</span>` : ""}</span></label>`;
    }).join("") : '<p class="muted">No durable facts found in those sources.</p>';
  }
  $("d-all").onclick = () => $("d-list").querySelectorAll("input").forEach((c) => { c.checked = !dRes.items[+c.dataset.i].duplicate; });
  $("d-none").onclick = () => $("d-list").querySelectorAll("input").forEach((c) => { c.checked = false; });
  $("d-save").onclick = async (e) => {
    const notes = [...$("d-list").querySelectorAll("input:checked")].map((c) => { const it = dRes.items[+c.dataset.i]; return { text: it.text, context: it.context, source: it.source }; });
    if (!notes.length) return;
    const b = e.target; b.disabled = true; b.innerHTML = `<span class="spin"></span>Saving ${notes.length}…`;
    try {
      const r = await api("/api/discover/commit", { notes });
      $("d-review").classList.add("hidden"); $("d-done").classList.remove("hidden");
      $("d-done").innerHTML = `<p>Saved ${r.saved} notes ✓${r.skipped ? ` · ${r.skipped} skipped (already there)` : ""}${r.failed ? ` · ${r.failed} failed` : ""}. They are tagged “import-auto”.</p>
        <button class="mbtn" id="d-undo">Undo this import</button>`;
      $("d-undo").onclick = async (ev) => { ev.target.disabled = true; const u = await api("/api/discover/undo", { batch: r.batch }); $("d-done").innerHTML = `<p>Removed ${u.removed} notes (each one can still be restored from its history).</p>`; };
    } catch (err) { alert("Not saved: " + err.message); }
    b.disabled = false; b.textContent = "Save selected";
  };
  document.querySelectorAll("[data-cloud]").forEach((b) => { b.onclick = async () => {
    try { await navigator.clipboard.writeText(CLOUD_PROMPT); $("c-msg").textContent = "Question copied ✓ — paste it in the chat that just opened"; }
    catch { $("c-msg").textContent = "Copy this question: " + CLOUD_PROMPT; }
    window.open(b.dataset.cloud, "_blank", "noopener");
  }; });
  $("c-go").onclick = (e) => previewJSON("claude", { text: $("c-text").value }, e.target);
  // first run: empty memory → invite to scan; opening Import with an empty memory scans right away
  async function emptyCheck() {
    try {
      const s = await api("/api/session");
      let total = 0;
      for (const c of s.contexts) total += ((await api("/api/memory/list?" + new URLSearchParams({ context: c, include_shared: "1" }))).items || []).length;
      $("empty-cta").classList.toggle("hidden", total > 0);
      return total === 0;
    } catch { return false; }
  }
  window.addEventListener("mnemos:memoria", emptyCheck);
  window.addEventListener("hashchange", async () => { if (location.hash === "#import" && !dSources.length && !dAuto && await emptyCheck()) { dAuto = true; dScan(); } });

  start();
})();
