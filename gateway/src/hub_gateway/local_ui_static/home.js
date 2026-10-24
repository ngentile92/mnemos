"use strict";
// Home: answers "is it working?" at a glance (state, memories, connected apps, skills), one next step, at most one
// gentle memory suggestion (accept or "not now"), and the latest activity.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p, b) => window.MNEMOS_API(p, b);
  const t = (x) => (window.t ? window.t(x) : x);
  const ASK = t("What do you know about me?");
  const when = (ts) => { const d = new Date(ts); return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); };
  const later = () => { try { return JSON.parse(sessionStorage.getItem("mnemos-tip-later") || "[]"); } catch { return []; } };
  let tip = null;

  async function load() {
    let s; try { s = await api("/api/session"); } catch { return; }
    const ctxs = s.contexts.filter((c) => c !== "shared");
    let notes = 0, skills = 0, apps = [];
    await Promise.all([
      ...s.contexts.map(async (c) => { try { notes += ((await api("/api/memory/list?" + new URLSearchParams({ context: c }))).items || []).length; } catch { /* skip */ } }),
      (async () => { try { skills = ((await api("/api/skills?" + new URLSearchParams({ context: ctxs[0] || "" }))).skills || []).length; } catch { /* skip */ } })(),
      (async () => { try { apps = ((await api("/api/apps")).apps || []).filter((a) => !a.revoked); } catch { /* skip */ } })(),
    ]);
    const live = apps.filter((a) => !a.paused);
    $("hm-n").textContent = notes; $("hm-a").textContent = live.length; $("hm-s").textContent = skills;
    const ok = live.length > 0;
    $("hm-title").textContent = ok ? t("Mnemos is working ✓") : t("Almost there");
    $("hm-sub").textContent = ok ? `${live.length === 1 ? "1 AI app uses" : live.length + " AI apps use"} your memory` : t("Connect an AI app so it can use your memory");
    $("hm-apps").innerHTML = apps.map((a) => `<span class="ap-pill">${esc(a.label)} ${a.paused ? "· paused" : "✓"}</span>`).join("");
    next(ok, notes);
    if (ok) mark("first_app");
    loadTip(); feed(); demo(ok);
  }

  function next(ok, notes) {
    let html;
    if (!ok) html = `<div class="hm-next"><p>${t("In the Mnemos window, press Connect next to the AI apps you use, then restart them. They show up here after their first message.")}</p><a class="mbtn primary" href="#apps">${t("See apps")}</a></div>`;
    else if (!notes) html = `<div class="hm-next"><p>${t("Your memory is empty. Mnemos can find what your AI apps already know about you; nothing is saved until you review it.")}</p><a class="mbtn primary" href="#import">${t("Find what my AIs know")}</a></div>`;
    else html = `<div class="hm-next"><p>${t("Try it: in any connected AI, ask")} <b>“${ASK}”</b></p><button class="mbtn primary" id="hm-copy">${t("Copy the question")}</button></div>`;
    $("hm-next-body").innerHTML = html;
    const b = $("hm-copy");
    if (b) b.onclick = async () => { try { await navigator.clipboard.writeText(ASK); b.textContent = t("Copied ✓"); } catch { b.textContent = ASK; } };
  }

  async function loadTip() {
    let j; try { j = await api("/api/hygiene"); } catch { return; }
    const skip = later();
    tip = (j.items || []).find((i) => !skip.includes(i.key)) || null;
    $("hm-tip").classList.toggle("hidden", !tip);
    if (!tip) return;
    const main = (tip.actions || []).find((a) => a.kind === "primary") || (tip.actions || [])[0];
    $("hm-tip-body").innerHTML = `<div class="why">${esc(tip.title)}</div><p class="small muted">${esc(tip.explain || "")}</p>
      ${(tip.notes || []).slice(0, 2).map((n) => `<div class="me-inote"><div>${esc(n.text)}</div></div>`).join("")}
      ${tip.proposed_text ? `<div class="me-inote ok"><span class="small muted">suggested</span><div>${esc(tip.proposed_text)}</div></div>` : ""}
      <div class="me-actions">${main ? `<button class="mbtn primary" id="hm-tip-ok" data-a="${esc(main.id)}">${esc(main.label)}</button>` : ""}
        <button class="mbtn" id="hm-tip-later">${t("Not now")}</button><a class="small" href="#memoria" style="margin-left:auto">${t("See all in Memory")}</a></div>`;
    const okb = $("hm-tip-ok");
    if (okb) okb.onclick = async () => {
      okb.disabled = true;
      try { await api("/api/hygiene/act", { context: tip.context, key: tip.key, action: okb.dataset.a }); } catch (e) { okb.disabled = false; okb.textContent = "Not done: " + e.message; return; }
      loadTip();
    };
    $("hm-tip-later").onclick = () => { sessionStorage.setItem("mnemos-tip-later", JSON.stringify([...skip, tip.key])); loadTip(); };
  }

  async function feed() {
    let r; try { r = await api("/api/activity?" + new URLSearchParams({ limit: 5 })); } catch { return; }
    $("hm-feed").innerHTML = r.events.length ? r.events.map((e) => `<div class="ap-ev"><span class="muted">${esc(when(e.ts))}</span><b>${esc(e.label)}</b><span>${esc(e.summary)}</span></div>`).join("")
      : '<p class="small muted">${t("Nothing yet. What your AI apps read or save shows up here.")}</p>';
  }

  // local first-run timing (never sent anywhere)
  const marked = new Set();
  function mark(step) { if (!marked.has(step)) { marked.add(step); api("/api/ftue", { step }).catch(() => {}); } }

  // guided "aha": save in one AI, ask in another. Ticks itself from the activity feed; can be skipped.
  let demoPoll = null;
  async function demo(ok) {
    clearTimeout(demoPoll);
    let f; try { f = await api("/api/ftue"); } catch { return; }
    const st = f.steps || {};
    const show = ok && !!st.welcome && !st.aha && !st.skipped;  // new memories only: older activity never ticks it
    $("hm-demo").classList.toggle("hidden", !show);
    if (!show) return;
    const since = st.welcome;
    let ev = []; try { ev = ((await api("/api/activity?" + new URLSearchParams({ limit: 100 }))).events || []).filter((e) => e.ok !== 0 && e.ok !== false && (!since || e.ts >= since)).reverse(); } catch { /* none */ }
    const save = ev.find((e) => e.kind === "write");
    const ask = save && ev.find((e) => e.kind === "read" && e.ts > save.ts && (e.app !== save.app || e.tool === "memory_search"));
    $("hm-d1").classList.toggle("done", !!save); $("hm-d2").classList.toggle("done", !!save);
    $("hm-d2-msg").textContent = save ? `Saved by ${save.label} ✓` : "Waiting for the save…";
    $("hm-d3").classList.toggle("done", !!ask);
    if (save) mark("first_save");
    if (ask) { mark("aha"); $("hm-demo-msg").textContent = `${ask.label} found it ✓ Every connected AI now shares this memory.`; return; }
    demoPoll = setTimeout(() => { if (!document.getElementById("view-home").classList.contains("hidden")) demo(ok); }, 4000);
  }
  document.addEventListener("click", async (e) => {
    const c = e.target.closest("[data-copy]");
    if (c) { try { await navigator.clipboard.writeText($(c.dataset.copy).textContent); c.textContent = t("Copied ✓"); } catch { /* visible text */ } }
    if (e.target.id === "hm-demo-skip") { mark("skipped"); $("hm-demo").classList.add("hidden"); clearTimeout(demoPoll); }
  });

  window.addEventListener("mnemos:view", (e) => { if (e.detail === "home") load(); });
})();
