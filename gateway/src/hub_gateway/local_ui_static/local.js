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
      history.replaceState(null, "", location.pathname);
      try { await api("/api/login", { token: m[1] }); } catch { /* fall through to the password form */ }
    }
    let s;
    try { s = await api("/api/session"); } catch { return showLogin(); }
    if (await needsWelcome(s)) return showWelcome(s);
    boot(s);
  }
  // first run: one optional field (the name every AI will use) and one button; people with notes skip it
  async function needsWelcome(s) {
    let w; try { w = await api("/api/welcome"); } catch { return false; }
    if (w.done) return false;
    let total = 0;
    try { for (const c of s.contexts) total += ((await api("/api/memory/list?" + new URLSearchParams({ context: c }))).items || []).length; }
    catch { return false; }
    if (total > 0) { api("/api/welcome", {}).catch(() => {}); return false; }  // existing memory: no welcome, no demo
    if (w.name) {  // the name was already given in the Mnemos window: ask it only once
      try { await api("/api/welcome", {}); await api("/api/ftue", { step: "welcome" }); } catch { /* still boot */ }
      return false;
    }
    window.MNEMOS_WELCOME = w;
    return true;
  }
  // first run (0.17): one path in four steps — you · what your AIs know (Save all) · connect · try it (the "aha")
  function showWelcome(s) {
    const w = window.MNEMOS_WELCOME || { spaces: [] };
    const T = (x) => (window.tr ? window.tr(x) : x);
    let started = new Date().toISOString();
    let poll = null;
    $("welcome").classList.remove("hidden");
    const step = (n) => {
      clearTimeout(poll);
      for (let i = 1; i <= 4; i++) $("wl-p" + i).classList.toggle("hidden", i !== n);
      document.querySelectorAll("#wl-steps li").forEach((li) => { const k = +li.dataset.s; li.className = k === n ? "on" : k < n ? "done" : ""; });
      if (n === 2) scan(); if (n === 3) apps(); if (n === 4) { started = new Date().toISOString(); aha(); }
    };
    const finish = (hash) => { clearTimeout(poll); $("welcome").classList.add("hidden"); location.hash = hash || ""; boot(s); };
    $("wl-name").value = w.name || "";
    $("wl-spaces").innerHTML = w.spaces.filter((x) => x.name !== "shared").map((x) => `<span><i style="background:${esc(x.color)}"></i>${esc(T(x.label))}</span>`).join("");
    const go = async () => {
      $("wl-err").textContent = ""; $("wl-go").disabled = true;
      try { await api("/api/welcome", { name: $("wl-name").value }); api("/api/ftue", { step: "welcome" }).catch(() => {}); step(2); }
      catch (e) { $("wl-err").textContent = e.message; }
      $("wl-go").disabled = false;
    };
    $("wl-go").onclick = go;
    $("wl-name").onkeydown = (e) => { if (e.key === "Enter") go(); };
    $("wl-name").focus();
    // step 2: scan, then one button saves everything new (duplicates and secrets are skipped)
    let found = [];
    async function scan() {
      $("wl-src").innerHTML = `<div class="wl-row"><span class="spin"></span>${esc(T("Looking on this computer…"))}</div>`;
      try { found = (await api("/api/discover/scan")).sources || []; } catch { found = []; }
      $("wl-src").innerHTML = found.length ? found.map((x) => `<div class="wl-row"><span class="ic">✦</span><span><b>${esc(x.label)}</b><br><span class="small muted">${x.count} ${esc(x.count === 1 ? x.unit.replace(/s$/, "") : x.unit)}</span></span><span class="r">${esc(T(x.status === "imported" ? "Imported ✓" : "Ready"))}</span></div>`).join("")
        : `<div class="wl-row"><span class="ic">∅</span><span>${esc(T("Nothing found on this computer yet, and that is normal: Claude and ChatGPT keep their memory in your account. After you connect one, Mnemos shows you how to ask it to fill your memory from your past chats."))}</span></div>`;
      const todo = found.filter((x) => x.status !== "imported");
      $("wl-save").textContent = T(todo.length ? "Save all" : "Continue"); $("wl-review").classList.toggle("hidden", !todo.length);
    }
    $("wl-save").onclick = async (e) => {
      const todo = found.filter((x) => x.status !== "imported");
      if (!todo.length) return step(3);
      const b = e.target; b.disabled = true; $("wl-prog").classList.remove("hidden"); $("wl-msg2").textContent = "";
      try {
        await api("/api/discover/preview", { sources: todo.map((x) => ({ id: x.id, context: x.context || "auto" })) });
        let j;
        for (;;) { await new Promise((r) => setTimeout(r, 900)); j = await api("/api/discover/status"); $("wl-bar").max = j.total || 1; $("wl-bar").value = j.done || 0; $("wl-phase").textContent = T(j.phase || ""); if (j.state !== "running") break; }
        if (j.state === "error") throw new Error(j.error);
        const notes = (j.result.items || []).filter((it) => !it.duplicate).map((it) => ({ text: it.text, context: it.context, source: it.source, source_id: it.source_id }));
        const r = notes.length ? await api("/api/discover/commit", { notes }) : { saved: 0 };
        $("wl-prog").classList.add("hidden");
        $("wl-msg2").textContent = T(`Saved ${r.saved} ✓`); $("wl-msg2").className = "small ok";
        setTimeout(() => step(3), 1100);
      } catch (err) { $("wl-prog").classList.add("hidden"); $("wl-msg2").textContent = err.message; $("wl-msg2").className = "small err"; }
      b.disabled = false;
    };
    $("wl-review").onclick = (e) => { e.preventDefault(); finish("#import"); };
    $("wl-skip2").onclick = () => step(3);
    // step 3: apps tick themselves as they call Mnemos
    async function apps() {
      let a = []; try { a = ((await api("/api/apps")).apps || []).filter((x) => !x.revoked); } catch { /* none yet */ }
      $("wl-apps").innerHTML = a.map((x) => `<div class="wl-row ok"><span class="ic">✓</span><b>${esc(x.label)}</b><span class="r">${esc(T("Connected ✓"))}</span></div>`).join("")
        + (a.length ? "" : `<div class="wl-row"><span class="spin"></span>${esc(T("Waiting for the first AI app…"))}</div>`);
      $("wl-go3").textContent = T(a.length ? "Continue" : "Continue anyway");
      poll = setTimeout(apps, 3000);
    }
    $("wl-go3").onclick = () => step(4);
    $("wl-skip3").onclick = () => finish("");
    // step 4: copy the question; tick when any AI reads the memory
    $("wl-copy").onclick = async (e) => { try { await navigator.clipboard.writeText($("wl-q").textContent); e.target.textContent = T("Copied ✓"); } catch { /* visible */ } };
    async function aha() {
      let ev = []; try { ev = (await api("/api/activity?" + new URLSearchParams({ limit: 50 }))).events || []; } catch { /* none */ }
      const hit = ev.find((x) => x.kind === "read" && Date.parse(x.ts) >= Date.parse(started) - 2000 && x.ok !== 0 && x.ok !== false);
      if (hit) {
        $("wl-wait").classList.add("ok");
        $("wl-wait-t").textContent = `${hit.label} ${T("just read your memory ✓")}`;
        api("/api/ftue", { step: "aha" }).catch(() => {});
        return;
      }
      poll = setTimeout(aha, 3000);
    }
    $("wl-done").onclick = () => finish("");
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
    window.MNEMOS = { contexts: s.contexts.map((name) => ({ name })), labs: !!s.labs, store: s.store || "notes" };
    document.body.classList.toggle("nolabs", !s.labs);
    $("labs-on").checked = !!s.labs;
    if (s.labs_forced) { $("labs-on").disabled = true; $("labs-head").textContent = "set by MNEMOS_LABS"; }
    $("labs-on").onchange = async () => {
      try { const r = await api("/api/labs", { on: $("labs-on").checked }); document.body.classList.toggle("nolabs", !r.on); window.MNEMOS.labs = r.on;
        $("labs-msg").textContent = r.on ? "On ✓" : "Off ✓"; } catch (e) { $("labs-msg").textContent = e.message; }
    };
    $("app").classList.remove("hidden"); $("tabs").classList.remove("hidden");
    for (const id of ["g-ctx", "s-ctx", "i-ctx"]) $(id).innerHTML = s.contexts.map((c) => `<option>${esc(c)}</option>`).join("");
    const sc = document.createElement("script");
    sc.src = "/ui/memory.js";
    sc.onload = () => route();
    document.body.appendChild(sc);
    window.addEventListener("hashchange", route);
    loadEngine();
  }
  const loaded = {};
  // v0.18: four places after the first run — Your map, Memory, AIs, Settings. Spaces, Skills, What each AI knows and
  // Bring in more are pages inside Settings (the Settings tab stays lit there). No hash: your map once there are
  // notes, otherwise AIs (connect one first). Old links (#home) land on the same default.
  const VIEWS = ["memoria", "graph", "skills", "spaces", "native", "apps", "import", "settings"];
  const IN_SETTINGS = ["skills", "spaces", "native", "import"];
  let firstView = null;
  async function defaultView() {
    if (firstView) return firstView;
    let n = 0; try { n = (await api("/api/memory/counts")).total || 0; } catch { /* treat as empty */ }
    firstView = n > 0 ? "graph" : "apps";
    return firstView;
  }
  async function route() {
    const v = location.hash.slice(1).split(/[?&]/)[0];
    const view = VIEWS.includes(v) ? v : await defaultView();
    document.querySelectorAll(".view").forEach((el) => el.classList.toggle("hidden", el.id !== "view-" + view));
    const lit = IN_SETTINGS.includes(view) ? "settings" : view;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === lit));
    if (!loaded[view]) {
      loaded[view] = true;
      if (view === "memoria") window.dispatchEvent(new Event("mnemos:memoria"));
      if (view === "graph") loadGraph();
      if (view === "skills") loadSkills();
    }
    if (view === "settings") { loadEngine(); loadFtue(); }
    if (view === "import" && window.mnemosImportOpen) window.mnemosImportOpen();
    window.dispatchEvent(new CustomEvent("mnemos:view", { detail: view }));
  }
  window.MNEMOS_ROUTE = route;

  async function loadFtue() {
    let f; try { f = await api("/api/ftue"); } catch { return; }
    const L = { first_app: "First AI app connected", first_save: "First memory saved by an AI", aha: "Asked in another AI (aha)", skipped: "Skipped the demo" };
    const rows = Object.entries(f.minutes || {}).map(([k, m]) => `<div>${esc(L[k] || k)}: <b>${m} min</b></div>`);
    $("ft-out").innerHTML = f.steps && f.steps.welcome ? (rows.join("") || '<p class="muted">Nothing yet after the welcome.</p>') : '<p class="muted">No first run recorded on this memory.</p>';
  }
  // ------------------------------------------------ graph (graph.js: canvas + physics)
  let gInit = false;
  function loadGraph() {
    if (!$("g-ctx").querySelector('option[value="*"]')) $("g-ctx").insertAdjacentHTML("beforeend", '<option value="*">All spaces</option>');
    if (!gInit) { gInit = true; if ($("g-ctx").options.length > 2) $("g-ctx").value = "*"; }  // galaxies side by side
    if (window.MnemosGraph) window.MnemosGraph.load();
  }

  // ------------------------------------------------ skills
  let skills = [];
  async function loadSkills() {
    try { skills = (await api("/api/skills?" + new URLSearchParams({ context: $("s-ctx").value }))).skills; }
    catch (e) { $("s-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    $("s-list").innerHTML = skills.length ? skills.map((s, i) => `<div class="me-item" data-i="${i}"><div class="m"><span>${esc(s.owner)}</span></div><div class="t"><b>${esc(s.name)}</b> — ${esc(s.description)}</div>
        ${s.try ? `<button class="mbtn s-try" data-i="${i}" title="Copies a sentence to paste in any connected AI">Try it in your AI</button>` : ""}</div>`).join("")
      : '<p class="muted">No skills yet.</p><div class="me-actions"><button class="mbtn primary" id="s-examples2">Add 3 examples</button></div>';
    if ($("s-examples2")) $("s-examples2").onclick = addExamples;
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
  $("s-list").addEventListener("click", async (ev) => {
    const t = ev.target.closest(".s-try");
    if (t) {
      ev.stopPropagation();
      try { await navigator.clipboard.writeText(skills[+t.dataset.i].try); t.textContent = "Copied ✓ — paste it in your AI"; } catch { t.textContent = skills[+t.dataset.i].try; }
      return;
    }
    const n = ev.target.closest(".me-item"); if (n) showSkill(skills[+n.dataset.i]);
  });
  async function addExamples() {
    try { const r = await api("/api/skills/examples", {}); $("s-detail").innerHTML = `<p class="small">${r.added.length ? `Added ${r.added.map(esc).join(", ")} ✓ — press “Try it in your AI” on one.` : "The examples are already there."}</p>`; }
    catch (e) { $("s-detail").innerHTML = `<p class="err small">${esc(e.message)}</p>`; }
    setTimeout(loadSkills, 600);
  }
  $("s-examples").onclick = addExamples;
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
      const sm = r.summary || { todo: dSources.length, up_to_date: false };
      $("d-sum").textContent = `Found ${dSources.length} source${dSources.length === 1 ? "" : "s"}`;
      $("d-status").className = "imp-status " + (dSources.length && sm.up_to_date ? "ok" : "todo");
      $("d-status").textContent = !dSources.length ? "" : sm.up_to_date ? "Everything is up to date ✓ Nothing new to import."
        : `${sm.todo} source${sm.todo === 1 ? " has" : "s have"} something new to import`;
      const en = r.engine || { kind: "rules" };
      $("d-llm").textContent = en.kind === "api" ? `Facts are found with ${en.name}: the selected text is sent to that provider.`
        : en.kind === "rules" ? "Simple extraction (first-person facts and instruction files). For better results: Settings → Improve my memory."
        : `Facts are found with ${en.name}, on this computer.`;
      const T = window.tr || ((x) => x);
      const pill = (s) => s.status === "imported" ? `<span class="pill ok">${esc(T(`Imported ✓ ${dDate(s.last_import)} · ${s.saved} facts`))}</span>`
        : s.status === "changed" ? `<span class="pill new">${esc(T(`${s.new_files} new or changed since ${dDate(s.last_import)}`))}</span>`
        : `<span class="pill new">${esc(T("New · never imported"))}</span>`;
      $("d-sources").innerHTML = dSources.length ? dSources.map((s, i) => `<label class="imp-item${s.status === "imported" ? " done" : ""}"><input type="checkbox" data-i="${i}" ${s.status === "imported" ? "" : "checked"}>
        <span style="flex:1"><b>${esc(s.label)}</b> ${pill(s)}<br><span class="small muted">${s.count} ${esc((window.tr || ((x) => x))(s.count === 1 ? s.unit.replace(/s$/, "") : s.unit))} · ${kb(s.bytes)} · ${esc(String(s.where).split(", ").map((w) => (w === "~" ? (window.tr || ((x) => x))("home folder") : w)).join(", "))}</span></span>
        <select data-c="${i}" aria-label="Context">${dCtxs.length > 1 ? `<option value="auto" ${s.context === "auto" ? "selected" : ""}>${esc((window.tr || ((x) => x))("Sort by content"))}</option>` : ""}${dCtxs.map((c) => `<option ${c === s.context ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>`).join("")
        : '<p class="muted small">Nothing found on this computer. Use the options below.</p>';
      $("d-go-row").classList.toggle("hidden", !dSources.length);
      // little or nothing here: say why per app, and route to "Fill your memory from your past chats"
      const why = $("d-why");
      if (r.little && r.apps) {
        const ic = { found: "✓", empty: "·", absent: "–", cloud: "☁" };
        why.innerHTML = `<p class="small"><b>${esc(T(dSources.length ? "Only a little to import from this computer." : "Nothing to import from this computer, and that's normal."))}</b> ${esc(T("Here is why, app by app:"))}</p>
          <div class="imp-why">${r.apps.map((a) => `<div><span>${ic[a.state] || "·"}</span><b>${esc(a.name)}</b><span class="small muted">${esc(T(a.state === "found" ? "Found above." : a.why))}</span></div>`).join("")}</div>
          <p class="small">${esc(T("Your AIs still know you from your chats with them. Connect one, then ask it to save what it knows into Mnemos."))}</p>
          <div class="me-actions"><a class="mbtn primary" href="#apps" id="d-fill">${esc(T("Fill my memory from my past chats"))}</a></div>`;
        why.classList.remove("hidden");
        if (!dSources.length) $("d-sources").innerHTML = "";
      } else why.classList.add("hidden");
    } catch (e) { alert("Scan failed: " + e.message); }
    b.disabled = false; b.textContent = "Scan again";
  }
  const dDate = (iso) => { try { return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }); } catch { return ""; } };
  async function dHistory() {
    let h = []; try { h = (await api("/api/discover/history")).history || []; } catch { return; }
    $("d-hist").innerHTML = h.length ? `<details><summary class="small">Import history (${h.length})</summary>${h.slice(0, 10).map((x) => `<div class="imp-item"><span style="flex:1">${esc(dDate(x.at))} · ${esc((x.sources || []).join(", "))}<br><span class="small muted">${x.saved} saved · ${x.skipped} already in your memory${x.undone ? ` · undone (${x.removed || 0} removed)` : ""}</span></span>${x.undone || !x.saved ? "" : `<button class="mbtn" data-undo="${esc(x.batch)}">Undo</button>`}</div>`).join("")}</details>` : "";
  }
  $("d-hist").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-undo]"); if (!b) return;
    if (!confirm("Remove the notes saved by this import? Each one can still be restored from its history.")) return;
    b.disabled = true; try { await api("/api/discover/undo", { batch: b.dataset.undo }); } catch (e) { alert(e.message); }
    dHistory();
  });
  $("d-scan").onclick = dScan;
  window.mnemosImportOpen = () => { if (!dSources.length) dScan(); dHistory(); };
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
    const groups = {};
    r.items.forEach((it, i) => { (groups[it.source_label] = groups[it.source_label] || []).push(i); });
    const row = (i) => { const it = r.items[i];
      return `<label class="imp-item${it.duplicate ? " dup" : ""}"><input type="checkbox" data-i="${i}" ${it.duplicate ? "" : "checked"}>
        <span>${esc(it.text)}${it.duplicate ? ` <span class="badge obs">${esc((window.tr || ((x) => x))("already in your memory"))}</span>` : ""}${it.route ? `<br><span class="small muted">→ ${esc(it.context)} · ${esc(it.route.reason || "")}${it.route.review ? " · review" : ""}</span>` : ""}</span>
        ${it.route ? `<select class="d-sp" data-i="${i}" aria-label="Space">${dCtxs.map((c) => `<option ${c === it.context ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>` : ""}</label>`; };
    const names = Object.keys(groups);
    $("d-list").innerHTML = names.length ? names.map((n, gi) => { const ix = groups[n], nw = ix.filter((i) => !r.items[i].duplicate).length;
      return `<details class="imp-group" ${names.length === 1 ? "open" : ""}><summary><b>${esc(n)}</b> <span class="small muted">${nw} new · ${ix.length - nw} already in your memory</span>
        <span class="grp-acts"><button class="mbtn" data-g="${gi}" data-on="1">Select all new</button><button class="mbtn" data-g="${gi}" data-on="0">Select none</button></span></summary>${ix.map(row).join("")}</details>`; }).join("")
      : '<p class="muted">No durable facts found in those sources.</p>';
    $("d-list").querySelectorAll("[data-g]").forEach((b) => { b.onclick = (ev) => { ev.preventDefault();
      b.closest("details").querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = b.dataset.on === "1" && !dRes.items[+c.dataset.i].duplicate; }); }; });
  }
  $("d-list").addEventListener("change", (ev) => { const t = ev.target; if (t.classList.contains("d-sp")) dRes.items[+t.dataset.i].context = t.value; });
  $("d-all").onclick = () => $("d-list").querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = !dRes.items[+c.dataset.i].duplicate; });
  $("d-none").onclick = () => $("d-list").querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = false; });
  $("d-save").onclick = async (e) => {
    const notes = [...$("d-list").querySelectorAll("input:checked")].map((c) => { const it = dRes.items[+c.dataset.i]; return { text: it.text, context: it.context, source: it.source, source_id: it.source_id }; });
    if (!notes.length) return;
    const b = e.target; b.disabled = true; b.innerHTML = `<span class="spin"></span>Saving ${notes.length}…`;
    try {
      const r = await api("/api/discover/commit", { notes });
      $("d-review").classList.add("hidden"); $("d-done").classList.remove("hidden");
      $("d-done").innerHTML = `<p>Saved ${r.saved} notes ✓${r.skipped ? ` · ${r.skipped} skipped (already there)` : ""}${r.failed ? ` · ${r.failed} failed` : ""}. They are tagged “import-auto”.</p>
        <button class="mbtn" id="d-undo">Undo this import</button>`;
      $("d-undo").onclick = async (ev) => { ev.target.disabled = true; const u = await api("/api/discover/undo", { batch: r.batch }); $("d-done").innerHTML = `<p>Removed ${u.removed} notes (each one can still be restored from its history).</p>`; dHistory(); };
      dHistory(); dScan();
    } catch (err) { alert("Not saved: " + err.message); }
    b.disabled = false; b.textContent = "Save selected";
  };
  document.addEventListener("click", (ev) => { if (ev.target.closest("#d-fill")) sessionStorage.setItem("mnFill", "1"); });
  document.querySelectorAll("[data-cloud]").forEach((b) => { b.onclick = async () => {
    try { await navigator.clipboard.writeText(CLOUD_PROMPT); $("c-msg").textContent = "Question copied ✓ — paste it in the chat that just opened"; }
    catch { $("c-msg").textContent = "Copy this question: " + CLOUD_PROMPT; }
    window.open(b.dataset.cloud, "_blank", "noopener");
  }; });
  $("c-go").onclick = (e) => previewJSON("claude", { text: $("c-text").value }, e.target);
  $("c-block").onclick = async () => {
    $("c-block-msg").textContent = "";
    try {
      const r = await api("/api/cloud/block");
      await navigator.clipboard.writeText(r.block);
      $("c-block-msg").textContent = `Copied ✓ (${r.block.length} characters) — paste it in the app's settings`;
    } catch (e) { $("c-block-msg").textContent = "Could not copy: " + e.message; }
  };
  // ------------------------------------------------ full-history mining (background, resumable)
  let mPoll = null, mNew = [];
  async function mLoad() {
    let m; try { m = await api("/api/mine"); } catch { return; }
    const run = m.state === "running";
    $("m-sum").textContent = m.found ? `${m.found} found · ${m.saved} saved · ${m.dismissed} dismissed` : "";
    $("m-pause").classList.toggle("hidden", !run);
    $("m-resume").classList.toggle("hidden", !(m.state === "paused" || m.state === "error") || !m.sources.length);
    $("m-go").disabled = run;
    $("m-progress").classList.toggle("hidden", !(run || m.state === "paused"));
    $("m-bar").max = m.total || 1; $("m-bar").value = m.done || 0;
    $("m-phase").textContent = run ? `${m.phase || "Reading"} · ${m.done || 0} of ${m.total || "?"} batches` : m.state === "paused" ? `Paused · ${m.read_batches} batches read` : "";
    $("m-msg").textContent = m.state === "error" ? "Stopped: " + (m.error || "") : m.state === "done" ? "Up to date ✓" : "";
    const ids = mNew.map((x) => x.id).join(); mNew = m.new || [];
    $("m-review").classList.toggle("hidden", !mNew.length);
    if (ids !== mNew.map((x) => x.id).join()) {
      const keep = new Set([...$("m-list").querySelectorAll("input:not(:checked)")].map((c) => c.dataset.id));
      let last = "";
      $("m-list").innerHTML = mNew.map((it) => {
        const head = it.source_label + it.context !== last ? `<h3 class="small" style="margin:14px 0 4px">${esc(it.source_label)} → ${esc(it.context)}</h3>` : "";
        last = it.source_label + it.context;
        return head + `<label class="imp-item"><input type="checkbox" data-id="${esc(it.id)}" ${keep.has(it.id) ? "" : "checked"}><span>${esc(it.text)}</span></label>`;
      }).join("");
    }
    const ev = Object.values(m.evidence || {}).reduce((a, n) => a + n, 0);
    $("m-ev").classList.toggle("hidden", !ev);
    $("m-ev-n").textContent = `${ev} passages of your own messages are searchable, so AI apps can find what you said (kept only in your Mnemos folder).`;
    clearTimeout(mPoll);
    if (run) mPoll = setTimeout(mLoad, 2000);
  }
  const mPicked = () => [...$("m-list").querySelectorAll("input:checked")].map((c) => c.dataset.id);
  $("m-go").onclick = async () => {
    if (!dSources.length) await dScan();
    const chosen = [...$("d-sources").querySelectorAll("input:checked")].map((c) => ({ id: dSources[+c.dataset.i].id, context: $("d-sources").querySelector(`select[data-c="${c.dataset.i}"]`).value }));
    try { await api("/api/mine/start", { sources: chosen }); } catch (e) { $("m-msg").textContent = e.message; return; }
    mLoad();
  };
  $("m-pause").onclick = async () => { await api("/api/mine/pause", {}); mLoad(); };
  $("m-resume").onclick = async () => { try { await api("/api/mine/resume", {}); } catch (e) { $("m-msg").textContent = e.message; } mLoad(); };
  $("m-forget").onclick = async () => { if (!confirm("Delete the searchable copy of your source messages? Saved notes stay.")) return; try { await api("/api/mine/forget-sources", {}); } catch (e) { alert(e.message); } mLoad(); };
  $("m-none").onclick = () => $("m-list").querySelectorAll("input").forEach((c) => { c.checked = false; });
  $("m-save").onclick = async (e) => {
    const ids = mPicked(); if (!ids.length) return;
    const b = e.target; b.disabled = true; b.innerHTML = `<span class="spin"></span>Saving ${ids.length}…`;
    try { const r = await api("/api/mine/save", { ids }); $("m-msg").textContent = `Saved ${r.saved} ✓${r.skipped ? ` · ${r.skipped} skipped (already there)` : ""}`; }
    catch (err) { alert("Not saved: " + err.message); }
    b.disabled = false; b.textContent = "Save selected"; mNew = []; mLoad();
  };
  $("m-dismiss").onclick = async () => { const ids = mPicked(); if (ids.length) { await api("/api/mine/dismiss", { ids }); mNew = []; mLoad(); } };
  window.addEventListener("hashchange", () => { if (location.hash === "#import") mLoad(); });
  if (location.hash === "#import") mLoad();

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

  
  // ---------------------------------------------------------------- engine: built-in model, Ollama, API key
  const WHERE = { rules: "Simple rules (instant, on this Mac)", builtin: "Built-in model (on this Mac)", ollama: "Local model (on this Mac)", api: "Your API key (text leaves this Mac)", apple: "Apple Intelligence (on this Mac)" };
  const gb = (n) => n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : Math.round(n / 1e6) + " MB";
  let eng = null, engPoll = null;
  async function loadEngine() {
    try { eng = await api("/api/engine"); } catch { return; }
    const a = eng.active, b = eng.builtin, d = b.download || {};
    $("st-active").innerHTML = [["Finding facts", a.import], ["Cleaning up", a.hygiene], ["Searching", a.search]]
      .map(([k, e]) => `<div class="me-actions"><span class="small"><b>${esc(k)}</b> · ${esc((window.tr || ((x) => x))(WHERE[e.kind] || e.kind))}</span><span class="muted small" title="${esc(e.name)}"></span></div>`).join("");
    $("st-models").innerHTML = b.models.map((m) => `<p class="small">${m.present ? "✓" : "○"} ${esc(m.name)} · ${gb(m.size)} · ${esc(m.license || "")} <span class="muted">(${m.role === "llm" ? "facts and cleanup" : "search and duplicates"})</span></p>`).join("");
    const busy = d.state === "downloading";
    $("st-bstate").textContent = !b.runtime ? "not included in this install" : b.ready ? "installed" : b.upgrade ? (busy ? "upgrading to the better model — the previous one keeps working meanwhile" : "previous model in use — download the better one") : busy ? "downloading" : b.choice === "declined" ? "not downloaded (you said not now)" : "not downloaded";
    $("st-dl").classList.toggle("hidden", b.ready || !b.runtime); $("st-dl").disabled = busy;
    $("st-dl").textContent = d.state === "error" ? "Retry the download (it resumes)" : `Improve my memory (download ${gb(b.size)})`;
    $("st-rm").classList.toggle("hidden", !b.ready && !(b.models.some((m) => m.present)));
    $("st-bmsg").textContent = d.state === "error" ? "Download failed: " + (d.error || "") + " — Mnemos keeps using simple rules." : "";
    $("st-ollama").textContent = eng.ollama ? `Ollama is running (${eng.ollama}) and is used first for import and cleanup.` : "Ollama: not running (optional).";
    for (const [bar, ph, box] of [["st-bar", "st-phase", "st-progress"], ["eo-bar", "eo-phase", "eo-progress"]]) {
      $(box).classList.toggle("hidden", !busy);
      if (busy) { $(bar).value = d.total ? d.done / d.total : 0; $(ph).textContent = `${d.file || ""} · ${gb(d.done || 0)} of ${gb(d.total || 0)}`; }
    }
    const ap = eng.apple || {};
    $("st-apple").textContent = ap.available ? (ap.choice === "off" ? "available · off" : "available") : "not available on this Mac";
    $("st-apple-on").checked = ap.choice !== "off"; $("st-apple-on").disabled = !ap.available;
    $("eo-size").textContent = gb(b.size);
    $("eng-offer").classList.toggle("hidden", !(b.runtime && !b.ready && (b.choice === "ask" || busy || (b.choice === "on" && d.state === "error"))));
    $("eo-go").disabled = busy; $("eo-no").classList.toggle("hidden", busy);
    $("eo-go").textContent = d.state === "error" ? "Retry (resumes)" : "Download";
    $("eo-msg").textContent = d.state === "error" ? "Download failed: " + (d.error || "") : "";
    if (b.ready && d.state === "ready") $("eo-msg").textContent = "";
    const p = eng.api;
    if (document.activeElement?.closest?.("#view-settings") == null) {
      $("st-prov").value = p.provider || ""; $("st-model").value = p.model || "";
      $("st-model").placeholder = p.defaults[$("st-prov").value] || "";
      $("st-key").placeholder = p.has_key ? "saved in the Keychain (type to replace)" : "sk-…";
      $("st-f-import").checked = p.import; $("st-f-hygiene").checked = p.hygiene;
    }
    clearTimeout(engPoll);
    if (busy) engPoll = setTimeout(loadEngine, 1000);
  }
  async function engDownload(btn) {
    btn.disabled = true;
    try { await api("/api/engine/download", {}); } catch (e) { $("st-bmsg").textContent = e.message; }
    loadEngine();
  }
  $("st-apple-on").onchange = async () => { await api("/api/engine/apple", { choice: $("st-apple-on").checked ? "auto" : "off" }); loadEngine(); };
  $("st-dl").onclick = () => engDownload($("st-dl"));
  $("eo-go").onclick = () => engDownload($("eo-go"));
  $("eo-no").onclick = async () => { await api("/api/engine/decline", {}); loadEngine(); };
  $("st-rm").onclick = async () => { if (!confirm("Remove the built-in model? Mnemos goes back to simple rules (or Ollama).")) return; await api("/api/engine/remove", {}); loadEngine(); };
  $("st-prov").onchange = () => { $("st-model").placeholder = (eng?.api.defaults || {})[$("st-prov").value] || ""; };
  async function apiSave(extra) {
    $("st-amsg").textContent = "";
    try {
      eng = await api("/api/engine/api", { provider: $("st-prov").value || null, key: $("st-key").value, model: $("st-model").value,
        import: $("st-f-import").checked, hygiene: $("st-f-hygiene").checked, ...extra });
      $("st-key").value = ""; $("st-amsg").textContent = "Saved ✓"; document.activeElement?.blur?.(); loadEngine();
    } catch (e) { $("st-amsg").textContent = e.message; }
  }
  $("st-save").onclick = () => apiSave({});
  $("st-clear").onclick = () => { $("st-prov").value = ""; $("st-f-import").checked = $("st-f-hygiene").checked = false; apiSave({ provider: null }); };
  $("st-test").onclick = async () => {
    $("st-amsg").textContent = "Testing…";
    try { const r = await api("/api/engine/api/test", {}); $("st-amsg").textContent = r.ok ? "The key works ✓" : "Did not work: " + (r.error || "no answer"); }
    catch (e) { $("st-amsg").textContent = e.message; }
  };
  start();
})();
