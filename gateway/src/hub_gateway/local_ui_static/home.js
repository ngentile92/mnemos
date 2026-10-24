"use strict";
// Home: answers "is it working?" at a glance (state, memories, connected apps, skills), one next step, at most one
// gentle memory suggestion (accept or "not now"), and the latest activity.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p, b) => window.MNEMOS_API(p, b);
  const ASK = "What do you know about me?";
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
    $("hm-title").textContent = ok ? "Mnemos is working ✓" : "Almost there";
    $("hm-sub").textContent = ok ? `${live.length === 1 ? "1 AI app uses" : live.length + " AI apps use"} your memory` : "Connect an AI app so it can use your memory";
    $("hm-apps").innerHTML = apps.map((a) => `<span class="ap-pill">${esc(a.label)} ${a.paused ? "· paused" : "✓"}</span>`).join("");
    next(ok, notes);
    loadTip(); feed();
  }

  function next(ok, notes) {
    let html;
    if (!ok) html = `<div class="hm-next"><p>Open <b>Mnemos Desktop → Connect apps</b>, pick the AI apps you use, then restart them. They show up here after their first message.</p><a class="mbtn primary" href="#apps">See apps</a></div>`;
    else if (!notes) html = `<div class="hm-next"><p>Your memory is empty. Mnemos can find what your AI apps already know about you; nothing is saved until you review it.</p><a class="mbtn primary" href="#import">Find what my AIs know</a></div>`;
    else html = `<div class="hm-next"><p>Try it: in any connected AI, ask <b>“${ASK}”</b></p><button class="mbtn primary" id="hm-copy">Copy the question</button></div>`;
    $("hm-next-body").innerHTML = html;
    const b = $("hm-copy");
    if (b) b.onclick = async () => { try { await navigator.clipboard.writeText(ASK); b.textContent = "Copied ✓"; } catch { b.textContent = ASK; } };
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
        <button class="mbtn" id="hm-tip-later">Not now</button><a class="small" href="#memoria" style="margin-left:auto">See all in Memory</a></div>`;
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
      : '<p class="small muted">Nothing yet. What your AI apps read or save shows up here.</p>';
  }

  window.addEventListener("mnemos:view", (e) => { if (e.detail === "home") load(); });
})();
