"use strict";
// Connect tab, top: one card per AI app (status + how to connect), ChatGPT / Claude web / Grok as first-class cards,
// and "Fill your memory from your past chats": a copy-paste request that makes a connected AI save what it
// already knows about you into Mnemos (for people whose computer had little or nothing to import).
(() => {
  const $ = (id) => document.getElementById(id);
  const T = (s) => (window.tr || ((x) => x))(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const es = () => /^es/i.test(document.documentElement.lang || navigator.language || "");
  const BOOT = {
    en: "Please look back over our past conversations and everything you remember about me. Save each lasting thing about me to my memory, one short note per fact, in plain words: who I am, my work and projects, the people and places that matter to me, my preferences (how I like answers, the apps I use, food, style) and my goals. Don't save passwords, keys or private numbers. When you finish, tell me how many notes you saved.",
    es: "Por favor revisá nuestras conversaciones anteriores y todo lo que recordás de mí. Guardá en mi memoria cada cosa duradera sobre mí, una nota corta por dato y en palabras simples: quién soy, mi trabajo y proyectos, las personas y lugares importantes para mí, mis preferencias (cómo me gustan las respuestas, las apps que uso, comida, estilo) y mis objetivos. No guardes contraseñas, claves ni números privados. Cuando termines, decime cuántas notas guardaste.",
  };
  const boot = () => BOOT[es() ? "es" : "en"];
  // local apps connect from the Mnemos window (it edits their config); cloud apps through a web address
  const APPS = [
    { key: "claude-desktop", name: "Claude Desktop", kind: "local" },
    { key: "chatgpt", name: "ChatGPT", kind: "cloud", open: "https://chatgpt.com/" },
    { key: "claude-web", name: "Claude on the web and phone", kind: "cloud", open: "https://claude.ai/new" },
    { key: "cursor", name: "Cursor", kind: "local" },
    { key: "claude-code", name: "Claude Code", kind: "local" },
    { key: "codex", name: "Codex", kind: "local" },
    { key: "grok", name: "Grok", kind: "cloud", open: "https://grok.com/" },
  ];
  const STEPS = {
    "chatgpt": ["In ChatGPT: Settings → Apps (or Connectors) → Advanced → turn on Developer mode.", "Create a new app (or “Add custom server”), paste the address below and choose OAuth.", "A Mnemos page opens: type your Mnemos password and pick the spaces ChatGPT may use."],
    "claude-web": ["In Claude: Settings → Connectors → Add custom connector.", "Paste the address below and press Connect.", "A Mnemos page opens: type your Mnemos password and pick the spaces Claude may use."],
    "grok": ["In Grok, add a remote MCP server (if your plan offers it) and paste the address below.", "Choose OAuth if asked; a Mnemos page opens for your password and spaces."],
  };
  let apps = [], remote = null;
  async function load() {
    try { apps = (await window.MNEMOS_API("/api/apps")).apps || []; } catch (_) { apps = []; }
    try { remote = await window.MNEMOS_API("/api/remote"); } catch (_) { remote = null; }
    render();
  }
  const seen = (k) => apps.find((a) => a.key === k && !a.revoked) || (k === "claude-web" && apps.find((a) => a.key === "claude-desktop" && a.via === "remote"));
  function card(a) {
    const on = seen(a.key);
    const pill = on ? `<span class="pill ok">${esc(T("Connected ✓"))}</span>` : `<span class="pill">${esc(T("Not connected yet"))}</span>`;
    let body = "";
    if (on) body = `<p class="small muted">${esc(T("It reads and saves your memory on its own."))}</p>`;
    else if (a.kind === "local") body = `<p class="small muted">${esc(T("In the Mnemos window, press Connect next to it, then restart the app and send it any message."))}</p>`;
    else {
      const url = remote && remote.enabled && remote.url;
      const steps = (STEPS[a.key] || []).map((s) => `<li>${esc(T(s))}</li>`).join("");
      body = url
        ? `<ol class="small cn-steps">${steps}</ol><div class="cn-url"><code>${esc(url)}</code><button class="mbtn" data-cncopy="${esc(url)}">${esc(T("Copy address"))}</button></div>`
        : `<p class="small muted">${esc(T("A live connection needs a web address for your memory. Turn on Reach from anywhere (Settings → Advanced), then come back here."))}</p>
           <div class="me-actions"><a class="mbtn" href="#settings" data-adv="1">${esc(T("Turn on Reach from anywhere"))}</a><a class="mbtn ghost" href="#import" data-cloudjump="1">${esc(T("Or copy and paste instead"))}</a></div>`;
    }
    return `<div class="cn-card${on ? " on" : ""}" data-k="${esc(a.key)}"><div class="cn-head"><b>${esc(T(a.name))}</b>${pill}</div>${body}</div>`;
  }
  function render() {
    $("cn-grid").innerHTML = APPS.map(card).join("");
    const conn = APPS.filter((a) => seen(a.key));
    $("bs-who").innerHTML = conn.length
      ? esc(T("Connected now:")) + " " + conn.map((a) => `<b>${esc(T(a.name))}</b>`).join(", ")
      : `<span class="warn">${esc(T("Connect at least one AI above first: it needs to be able to save to your memory."))}</span>`;
    $("bs-text").textContent = boot();
    $("bs-open").innerHTML = APPS.filter((a) => a.open && seen(a.key)).map((a) => `<a class="mbtn" href="${esc(a.open)}" target="_blank" rel="noopener noreferrer">${esc(T("Open " + a.name.split(" ")[0]))}</a>`).join("");
  }
  async function copy(text, out) {
    try { await navigator.clipboard.writeText(text); out.textContent = T("Copied ✓"); } catch (_) { out.textContent = T("Select the text and copy it"); }
    setTimeout(() => { out.textContent = ""; }, 4000);
  }
  document.addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-cncopy]"); if (c) { ev.preventDefault(); copy(c.dataset.cncopy, $("cn-msg")); }
    if (ev.target.closest("[data-adv]")) setTimeout(() => { const rc = $("rm-card"); if (rc) { const d = rc.closest("details"); if (d) d.open = true; rc.scrollIntoView({ block: "start" }); } }, 300);
    if (ev.target.closest("[data-cloudjump]")) setTimeout(() => { const c2 = $("cloud-card"); if (c2) c2.scrollIntoView({ block: "start" }); }, 400);
  });
  $("bs-copy").onclick = () => copy(boot(), $("bs-msg"));
  window.MNEMOS_BOOTSTRAP = boot;
  const go = () => { if (location.hash.startsWith("#apps")) load(); if (location.hash.startsWith("#apps") && sessionStorage.getItem("mnFill")) { sessionStorage.removeItem("mnFill"); setTimeout(() => $("bs-card").scrollIntoView({ block: "start" }), 400); } };
  window.addEventListener("hashchange", go); go();
})();
