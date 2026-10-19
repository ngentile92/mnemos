"use strict";
// Memory model UI (local mode): profile card first, entity pages, "still true?" digest, "why this?" on a note,
// "not relevant" feedback, and the migration / tidy-up controls in Settings.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p, b) => window.MNEMOS_API(p, b);
  const q = (o) => "?" + new URLSearchParams(o);
  const ctx = () => ($("me-ctx") && $("me-ctx").value) || "";
  const KIND = { me: "you", person: "person", project: "project", org: "organisation", place: "place", tool: "tool", topic: "topic" };
  function msg(id, t, bad) { const e = $(id); if (!e) return; e.textContent = t; e.style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  const day = (s) => String(s || "").slice(0, 10);

  // ---------------------------------------------------------------- profile card + entities + digest
  async function loadProfile() {
    const c = ctx(); if (!c) return;
    let p, e, d;
    try { [p, e, d] = await Promise.all([api("/api/model/profile" + q({ context: c })), api("/api/model/entities" + q({ context: c })),
      api("/api/model/digest" + q({ context: c }))]); } catch { $("pf-card").classList.add("hidden"); return; }
    const ents = e.entities || [];
    const show = (p.profile || "").trim() || ents.length;
    $("pf-card").classList.toggle("hidden", !show);
    if (!show) return;
    $("pf-head").textContent = ents.length ? `${ents.length} people, projects and things` : "";
    $("pf-text").textContent = (p.profile || "").trim() || "Not built yet: it appears after the nightly tidy-up (or Settings → Tidy up now).";
    $("pf-ents").innerHTML = ents.slice(0, 30).map((x) => `<button class="mbtn sm pf-e" data-id="${esc(x.id)}" title="${esc(KIND[x.kind] || x.kind || "")}">${esc(x.name)} <span class="muted">${x.n}</span></button>`).join("");
    const items = d.items || [];
    $("pf-digest-wrap").classList.toggle("hidden", !items.length);
    $("pf-digest-n").textContent = items.length ? `(${items.length})` : "";
    $("pf-digest").innerHTML = items.map((x) => `<div class="ap-row" data-id="${esc(x.note_id)}"><div class="ap-top"><div>${esc(x.text)}</div>
      <span class="small muted">${esc(x.entity || "")} · saved ${esc(day(x.recorded_at))}${x.source_app ? " · " + esc(x.source_app) : ""}</span></div>
      <div class="ap-ctl"><button class="mbtn sm dg" data-a="keep">Still true</button><button class="mbtn sm dg" data-a="fix">Fix…</button><button class="mbtn sm danger dg" data-a="forget">Forget</button></div></div>`).join("");
  }
  async function openEntity(id) {
    let r;
    try { r = await api("/api/model/entity" + q({ context: ctx(), id })); } catch (e) { return msg("pf-msg", e.message, true); }
    const f = (x, past) => `<div class="ap-row" data-id="${esc(x.note_id)}"><div class="ap-top"><div${past ? ' class="muted"' : ""}>${esc(x.text)}</div>
      <span class="small muted">${esc(x.kind || "")}${x.valid_to ? " · until " + esc(day(x.valid_to)) : ""} · saved ${esc(day(x.recorded_at))}${x.source_app ? " · " + esc(x.source_app) : ""}</span></div>
      ${past ? "" : '<div class="ap-ctl"><button class="mbtn sm nr">Not relevant</button></div>'}</div>`;
    const el = $("pf-entity");
    el.classList.remove("hidden");
    el.innerHTML = `<div class="card-h"><h3>${esc(r.entity.name)} <span class="muted small">${esc(KIND[r.entity.kind] || r.entity.kind || "")}</span></h3><button class="mbtn sm" id="pf-close">Close</button></div>
      ${r.entity.summary ? `<p class="small">${esc(r.entity.summary)}</p>` : ""}
      <div class="ap-list">${r.facts.map((x) => f(x, false)).join("") || '<p class="small muted">No current facts.</p>'}</div>
      ${r.past.length ? `<h4 class="small muted" style="margin-top:8px">No longer true</h4><div class="ap-list">${r.past.map((x) => f(x, true)).join("")}</div>` : ""}
      ${(r.evidence || []).length ? `<h4 class="small muted" style="margin-top:8px">From your chats</h4>${r.evidence.map((x) => `<p class="small muted">“${esc(String(x.text || x.snippet || "").slice(0, 240))}”</p>`).join("")}` : ""}`;
    $("pf-close").onclick = () => el.classList.add("hidden");
  }
  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    if (t.classList.contains("pf-e")) return openEntity(t.dataset.id);
    if (t.classList.contains("nr") || t.id === "why-nr") {
      const id = t.dataset.id || t.closest(".ap-row").dataset.id;
      try { await api("/api/model/not-relevant", { context: ctx(), note_id: id }); t.disabled = true; t.textContent = "Noted ✓"; } catch (e) { alert(e.message); }
      return;
    }
    if (t.classList.contains("dg")) {
      const id = t.closest(".ap-row").dataset.id, a = t.dataset.a;
      const body = { context: ctx(), note_id: id, action: a };
      if (a === "fix") { const cur = t.closest(".ap-row").querySelector(".ap-top div").textContent; const v = prompt("What is true now?", cur); if (!v) return; body.text = v; }
      if (a === "forget" && !confirm("Forget this note? You can undo it from the note panel.")) return;
      try { await api("/api/model/digest/act", body); msg("pf-msg", a === "keep" ? "Kept ✓" : a === "fix" ? "Fixed ✓" : "Forgotten"); loadProfile(); } catch (e) { msg("pf-msg", e.message, true); }
    }
  });

  // ---------------------------------------------------------------- "why this?" on the note panel
  function addWhy() {
    const d = $("me-detail"); if (!d || d.querySelector("#me-why")) return;
    const ids = d.querySelectorAll(".me-meta dd.small.muted"); const idEl = ids[ids.length - 1];
    const acts = d.querySelector("#me-actions"); if (!idEl || !acts) return;
    const b = document.createElement("button"); b.className = "mbtn"; b.id = "me-why"; b.textContent = "Why this?";
    acts.appendChild(b);
    b.onclick = async () => {
      let r;
      try { r = await api("/api/model/why" + q({ context: ctx(), note_id: idEl.textContent.trim() })); } catch (e) { return alert(e.message); }
      const rows = [["About", r.entity], ["Kind", r.kind], ["Importance", r.importance], ["Lasts", r.durability],
        ["From", [r.origin, r.source_app].filter(Boolean).join(" · ")], ["Saved", day(r.recorded_at)], ["Used", r.use_count ? `${r.use_count}× (last ${day(r.last_used_at)})` : "not yet"],
        ["Status", r.status + (r.valid_to ? ` until ${day(r.valid_to)}` : "") + (r.expires_at ? ` · expires ${day(r.expires_at)}` : "")], ["You said", r.quote]];
      const box = document.createElement("div"); box.className = "card"; box.style.marginTop = "10px";
      box.innerHTML = `<h3 class="small">Why Mnemos keeps and uses this</h3><dl class="me-meta">${rows.filter((x) => x[1] !== null && x[1] !== undefined && x[1] !== "")
        .map((x) => `<dt>${x[0]}</dt><dd>${esc(x[1])}</dd>`).join("")}</dl><button class="mbtn sm" id="why-nr" data-id="${esc(idEl.textContent.trim())}">Not relevant: use it less</button>`;
      b.replaceWith(box);
    };
  }
  const obs = new MutationObserver(addWhy);
  const hook = () => { const d = $("me-detail"); if (d) obs.observe(d, { childList: true, subtree: true }); };

  // ---------------------------------------------------------------- Settings → Memory model
  function fillCtx() {
    const src = $("me-ctx"), dst = $("mg-ctx"); if (!src || !dst) return;
    if (dst.options.length !== src.options.length) dst.innerHTML = src.innerHTML;
    if (!dst.value) dst.value = src.value;
  }
  async function migrate(apply) {
    const c = $("mg-ctx").value; msg("mg-msg", "Working…");
    try {
      const r = await api("/api/model/migrate", { context: c, apply });
      if (r.dry_run) {
        $("mg-out").innerHTML = `<p>${r.notes} notes would be linked (${r.already} already are).</p>
          <p class="muted">${Object.entries(r.kinds || {}).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ")}</p>
          <p>${(r.entities || []).slice(0, 25).map((e) => `${esc(e.name)} <span class="muted">(${esc(KIND[e.kind] || e.kind || "")}, ${e.facts})</span>`).join(", ")}</p>`;
        msg("mg-msg", "Nothing was changed.");
      } else {
        $("mg-out").innerHTML = `<p>${r.migrated} notes linked. <button class="mbtn sm" id="mg-undo" data-b="${esc(r.batch)}">Undo</button></p>`;
        msg("mg-msg", "Done ✓"); loadProfile();
      }
    } catch (e) { msg("mg-msg", e.message, true); }
  }
  $("mg-dry") && ($("mg-dry").onclick = () => migrate(false));
  $("mg-apply") && ($("mg-apply").onclick = () => confirm("Link every note of this space to who or what it is about? Notes stay as they are; you can undo it.") && migrate(true));
  $("mg-cons") && ($("mg-cons").onclick = async () => {
    msg("mg-msg", "Tidying up…");
    try {
      const r = await api("/api/model/consolidate", {});
      $("mg-jobs").innerHTML = Object.entries(r).map(([c, x]) => `<p>${esc(c)}: ${esc(Object.entries(x).filter(([k]) => k !== "job").map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · "))}
        <button class="mbtn sm mg-cu" data-c="${esc(c)}" data-j="${esc(x.job)}">Undo</button></p>`).join("");
      msg("mg-msg", "Done ✓"); loadProfile();
    } catch (e) { msg("mg-msg", e.message, true); }
  });
  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    try {
      if (t.id === "mg-undo") { const r = await api("/api/model/migrate/undo", { context: $("mg-ctx").value, batch: t.dataset.b }); msg("mg-msg", `Undone: ${r.facts} links removed.`); t.disabled = true; loadProfile(); }
      if (t.classList.contains("mg-cu")) { const r = await api("/api/model/consolidate/undo", { context: t.dataset.c, job: t.dataset.j }); msg("mg-msg", `Undone (${r.undone} changes).`); t.disabled = true; loadProfile(); }
    } catch (e) { msg("mg-msg", e.message, true); }
  });

  window.addEventListener("mnemos:memoria", () => { hook(); setTimeout(loadProfile, 300); });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "settings") fillCtx(); if (e.detail === "memoria") loadProfile(); });
  document.addEventListener("change", (e) => { if (e.target.id === "me-ctx") { $("pf-entity").classList.add("hidden"); loadProfile(); } });
  hook();
})();
