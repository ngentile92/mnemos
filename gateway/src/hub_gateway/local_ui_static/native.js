"use strict";
// What each AI knows (0.13 → 0.17): each AI app's own memory files exactly as stored. View them, compare them with
// Mnemos (which lines Mnemos already knows), edit them (a backup is saved first; nothing is written without a click),
// restore an earlier version, and see whether Mnemos keeps its own block in that app up to date.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const t = (x) => (window.t ? window.t(x) : x);
  const api = (p, b) => window.MNEMOS_API(p, b);
  let data = null, sync = null, app = null, file = null, mode = "view", cmp = null, draft = null;
  function msg(s, bad) { $("nm-msg").textContent = s; $("nm-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  const when = (s) => (s ? new Date(s).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
  const spaces = () => (((window.MNEMOS_SPACES || {}).spaces) || []).filter((s) => s.name !== "shared");
  const CLOUD = "cloud";
  async function load() {
    $("nm-files").innerHTML = '<p class="muted small"><span class="spin"></span>Looking at your apps…</p>';
    try { [data, sync] = await Promise.all([api("/api/native"), api("/api/sync").catch(() => null)]); } catch (e) { return msg(e.message, true); }
    const n = data.apps.reduce((a, x) => a + x.items.length, 0);
    $("nm-head").textContent = `${n} file${n === 1 ? "" : "s"}`;
    if (!app || (app !== CLOUD && !data.apps.find((a) => a.key === app))) app = (data.apps.find((a) => a.items.length) || data.apps[0]).key;
    const a = data.apps.find((x) => x.key === app);
    if (a && !a.items.find((x) => x.path === file)) file = null;
    render();
  }
  const cur = () => { const a = data.apps.find((x) => x.key === app); return a && a.items.find((x) => x.path === file); };
  const editable = (it) => it && !it.readonly_list && !/mnemos/i.test(it.kind) && !String(it.content).includes("[hidden by Mnemos");
  function syncLine(key) {
    const s = sync && sync.targets.find((x) => x.id === key);
    if (!s) return "";
    return `<div class="nm-sync ${s.on ? "on" : ""}"><span class="dot"></span><span>${s.on
      ? esc(t("Mnemos keeps its own block here up to date")) + (s.written_at ? ` · ${esc(t("updated"))} ${esc(when(s.written_at))}` : "")
      : esc(t("Mnemos is not writing to this app yet"))}</span><a class="mbtn sm" href="#apps">${esc(s.on ? t("Change") : t("Turn on in Apps"))}</a></div>`;
  }
  function render() {
    $("nm-apps").innerHTML = data.apps.map((a) => `<button class="mbtn nm-app ${a.key === app ? "primary" : ""}" data-k="${esc(a.key)}">${esc(a.label)} <span class="muted">${a.items.length}</span></button>`).join("")
      + `<button class="mbtn nm-app ${app === CLOUD ? "primary" : ""}" data-k="${CLOUD}">ChatGPT · Claude.ai · Grok</button>`;
    if (app === CLOUD) {
      $("nm-files").innerHTML = ""; $("nm-sync").innerHTML = "";
      $("nm-view").innerHTML = `<div class="nm-cloud"><h3>${esc(t("Their memory lives in the cloud"))}</h3>
        <p class="small muted">${esc(t("ChatGPT, Claude.ai and Grok keep what they remember in your account, not on this computer, so Mnemos can't read or change it directly. Two copy-and-paste steps keep them in sync: bring what they know into Mnemos, and give them a short block about you."))}</p>
        <a class="mbtn primary" href="#import">${esc(t("Open the copy-and-paste steps"))}</a></div>`;
      return;
    }
    const a = data.apps.find((x) => x.key === app);
    $("nm-sync").innerHTML = syncLine(app);
    $("nm-files").innerHTML = (a.items.length ? a.items.map((it, i) => `<a class="nm-file ${file === it.path ? "on" : ""}" data-i="${i}">
        <b>${esc(it.kind)}</b>${it.mnemos ? ` <span class="badge">${esc(t("written by Mnemos"))}</span>` : ""}<br><span class="small muted">${esc(it.where)}</span><br>
        <span class="small muted">${esc(t("modified"))} ${esc(when(it.modified))}</span></a>`).join("")
      : `<p class="muted small">${esc(a.label)} keeps no memory files on this computer (or it is not installed).</p>`);
    if (!file && a.items.length) file = a.items[0].path;
    const it = cur();
    if (!it) { $("nm-view").innerHTML = `<div class="empty"><b>${esc(t("Nothing to show yet"))}</b>${esc(t("When this app writes memory files on this computer, they appear here exactly as stored."))}</div>`; return; }
    const tabs = [["view", "View"], ...(it.readonly_list ? [] : [["compare", "Compare with Mnemos"]]), ...(editable(it) ? [["edit", "Edit"]] : [])];
    if (!tabs.some((x) => x[0] === mode)) mode = "view";
    const head = `<div class="nm-head"><div class="small muted nm-meta"><code>${esc(it.where)}</code> · ${esc(t("modified"))} ${esc(when(it.modified))}</div>
      <div class="nm-tabs">${tabs.map(([k, l]) => `<button class="nm-tab ${mode === k ? "on" : ""}" data-m="${k}">${esc(t(l))}</button>`).join("")}</div></div>`;
    $("nm-view").innerHTML = head + `<div id="nm-body"></div>`;
    if (mode === "view") return renderView(it);
    if (mode === "compare") return renderCompare(it);
    return renderEdit(it);
  }
  const pick = () => `<select class="nm-sp" aria-label="Space"><option value="auto">${esc(t("Sort by content"))}</option>${spaces().map((s) => `<option value="${esc(s.name)}">${esc(t(s.label))}</option>`).join("")}</select>`;
  function renderView(it) {
    $("nm-body").innerHTML = `${it.note ? `<p class="small">${esc(it.note)}</p>` : ""}
      ${it.readonly_list ? `<pre class="nm-raw">${esc(it.content)}</pre>` : it.sections.map((s, i) => `<div class="nm-sec" data-s="${i}">
        <div class="nm-sec-h"><b>${esc(s.title)}</b><span style="margin-left:auto"></span>${it.mnemos ? "" : `${pick()}<button class="mbtn nm-imp">${esc(t("Import into Mnemos"))}</button>`}</div>
        <pre class="nm-raw">${esc(s.text)}</pre></div>`).join("")}`;
  }
  async function renderCompare(it) {
    const b = $("nm-body");
    if (!cmp || cmp.path !== it.path) {
      b.innerHTML = `<p class="muted small"><span class="spin"></span>${esc(t("Comparing with your memory…"))}</p>`;
      try { cmp = { path: it.path, ...(await api("/api/native/compare", { path: it.path })) }; } catch (e) { b.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
      if (cur() !== it || mode !== "compare") return;
    }
    b.innerHTML = `<div class="nm-cmp-sum"><span class="pill ok">${esc(t(`${cmp.known} already in Mnemos`))}</span><span class="pill new">${esc(t(`${cmp.only_here} only in this file`))}</span>
        ${cmp.only_here && !it.mnemos ? `<span style="margin-left:auto">${pick()}</span>` : ""}</div>
      ${cmp.lines.length ? cmp.lines.map((l, i) => `<div class="nm-line ${l.known ? "known" : "new"}"><span class="nm-mark">${l.known ? "✓" : "+"}</span>
        <div class="nm-lt"><div class="ut">${esc(l.text)}</div>${l.known ? `<div class="small muted ut">${esc(t("In Mnemos:"))} ${esc(l.match)}</div>` : ""}</div>
        ${!l.known && !it.mnemos ? `<button class="mbtn sm nm-limp" data-l="${i}">${esc(t("Import"))}</button>` : ""}</div>`).join("")
      : `<p class="muted small">${esc(t("No facts to compare in this file."))}</p>`}`;
  }
  // line diff (LCS) for "Review changes"
  function diff(a, b) {
    const A = a.split("\n"), B = b.split("\n"), n = A.length, m = B.length;
    if (n * m > 4e6) return [{ op: "del", t: "(file too long to show the changes line by line)" }];
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = []; let i = 0, j = 0;
    while (i < n && j < m) { if (A[i] === B[j]) { out.push({ op: "eq", t: A[i] }); i++; j++; } else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: "del", t: A[i++] }); else out.push({ op: "add", t: B[j++] }); }
    while (i < n) out.push({ op: "del", t: A[i++] }); while (j < m) out.push({ op: "add", t: B[j++] });
    return out;
  }
  async function renderEdit(it) {
    if (!draft || draft.path !== it.path) draft = { path: it.path, text: it.content, review: false };
    const b = $("nm-body");
    if (!draft.review) {
      b.innerHTML = `<p class="small muted">${esc(t("You are editing the app's own file. Nothing is written until you review the changes and confirm; Mnemos saves a copy of the current file first."))}</p>
        <textarea id="nm-text" class="nm-edit" spellcheck="false">${esc(draft.text)}</textarea>
        <div class="me-actions"><button class="mbtn primary" id="nm-review">${esc(t("Review changes"))}</button><button class="mbtn" id="nm-cancel">${esc(t("Cancel"))}</button>
        <button class="mbtn" id="nm-hist" style="margin-left:auto">${esc(t("Earlier versions"))}</button></div><div id="nm-bk"></div>`;
      return;
    }
    const d = diff(it.content, draft.text), ch = d.filter((x) => x.op !== "eq").length;
    let ctxShown = d.map((x, k) => ({ ...x, k })).filter((x, k) => x.op !== "eq" || d.slice(Math.max(0, k - 2), k + 3).some((y) => y.op !== "eq"));
    b.innerHTML = `<p class="small"><b>${esc(t(`${ch} changed lines`))}</b> · ${esc(it.where)}</p>
      <div class="nm-diff">${ch ? ctxShown.map((x) => `<div class="${x.op}"><span>${x.op === "add" ? "+" : x.op === "del" ? "−" : " "}</span>${esc(x.t) || "&nbsp;"}</div>`).join("") : `<p class="muted small">${esc(t("No changes."))}</p>`}</div>
      <div class="small nm-ok"><input type="checkbox" id="nm-ok"><span class="nm-okl">${esc(t("Save a backup of the current file and write these changes"))}</span></div>
      <div class="me-actions"><button class="mbtn primary" id="nm-save" disabled>${esc(t("Save to file"))}</button><button class="mbtn" id="nm-back">${esc(t("Keep editing"))}</button></div>`;
    $("nm-body").querySelector(".nm-okl").onclick = () => { $("nm-ok").click(); };
    $("nm-ok").onchange = () => { $("nm-save").disabled = !$("nm-ok").checked || !ch; };
  }
  async function showBackups(it) {
    let r; try { r = await api("/api/native/backups", { path: it.path }); } catch (e) { return msg(e.message, true); }
    $("nm-bk").innerHTML = r.backups.length ? `<h3 class="small">${esc(t("Earlier versions"))}</h3>` + r.backups.map((x) => `<div class="imp-item"><span style="flex:1">${esc(when(x.at))} · ${esc(t(x.why))}</span><button class="mbtn sm" data-rb="${esc(x.file)}">${esc(t("Restore"))}</button></div>`).join("")
      : `<p class="small muted">${esc(t("No earlier versions yet: Mnemos keeps one each time you save here."))}</p>`;
  }
  document.addEventListener("click", async (ev) => {
    const el = ev.target;
    if (!el.closest || !el.closest("#view-native")) return;
    const it = data && cur();
    if (el.id === "nm-refresh") { cmp = null; return load(); }
    const b = el.closest(".nm-app"); if (b) { app = b.dataset.k; file = null; mode = "view"; return render(); }
    const f = el.closest(".nm-file"); if (f) { file = data.apps.find((x) => x.key === app).items[+f.dataset.i].path; return render(); }
    const tb = el.closest(".nm-tab"); if (tb) { mode = tb.dataset.m; return render(); }
    if (el.id === "nm-review") { draft.text = $("nm-text").value; draft.review = true; return renderEdit(it); }
    if (el.id === "nm-back") { draft.review = false; return renderEdit(it); }
    if (el.id === "nm-cancel") { draft = null; mode = "view"; return render(); }
    if (el.id === "nm-hist") return showBackups(it);
    if (el.id === "nm-save") {
      el.disabled = true; el.innerHTML = `<span class="spin"></span>${esc(t("Saving…"))}`;
      try { await api("/api/native/save", { path: it.path, text: draft.text, modified: it.modified, confirm: true }); draft = null; cmp = null; mode = "view"; msg(t("Saved ✓ A copy of the previous file is in Earlier versions.")); await load(); }
      catch (e) { el.disabled = false; el.textContent = t("Save to file"); msg(e.message, true); }
      return;
    }
    const rb = el.closest("[data-rb]");
    if (rb) {
      if (!confirm(t("Put this earlier version back? The current file is saved as a version first."))) return;
      try { await api("/api/native/restore", { path: it.path, file: rb.dataset.rb, confirm: true }); draft = null; cmp = null; mode = "view"; msg(t("Restored ✓")); await load(); }
      catch (e) { msg(e.message, true); }
      return;
    }
    if (el.classList.contains("nm-limp")) {
      const l = cmp.lines[+el.dataset.l], sp = $("nm-body").querySelector(".nm-sp"); el.disabled = true;
      try { const r = await api("/api/native/import", { path: it.path, line: l.text, app, context: sp ? sp.value : "auto" });
        const lab = (spaces().find((s) => s.name === r.context) || {}).label || r.context;
        l.known = true; l.match = l.text; cmp.known++; cmp.only_here--; renderCompare(it); msg(r.saved ? `${t("Saved to")} ${t(lab)} ✓` : t("You already had that.")); }
      catch (e) { el.disabled = false; msg(e.message, true); }
      return;
    }
    if (el.classList.contains("nm-imp")) {
      const sec = el.closest(".nm-sec"); el.disabled = true;
      try {
        const r = await api("/api/native/import", { path: file, section: +sec.dataset.s, app, context: sec.querySelector(".nm-sp").value });
        const lab = (spaces().find((s) => s.name === r.context) || {}).label || r.context;
        el.textContent = r.saved ? `${t("Imported →")} ${t(lab)}` : t("Already in Mnemos"); msg(r.saved ? `${t("Saved to")} ${t(lab)}${r.route && r.route.reason ? ` (${r.route.reason})` : ""}.` : t("You already had that."));
      } catch (e) { el.disabled = false; msg(e.message, true); }
    }
  });
  document.addEventListener("input", (ev) => { if (ev.target.id === "nm-text" && draft) draft.text = ev.target.value; });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "native" && !data) load(); });
})();
