"use strict";
// AIs tab, top: one card per AI (found on this Mac, connected, or copy and paste), connected from here,
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
  // v0.18: every AI connects from here (the Mnemos window has no Connect buttons any more). Desktop apps first:
  // one button writes the app's settings (backup first); the card turns Connected ✓ on the app's first call.
  let ais = [], remote = null, apps = [], poll = null;
  const pending = new Set();
  async function load() {
    clearTimeout(poll);
    try { const r = await window.MNEMOS_API("/api/connect"); ais = r.ais || []; remote = r.remote; } catch (_) { ais = []; }
    try { apps = (await window.MNEMOS_API("/api/apps")).apps || []; } catch (_) { apps = []; }
    render();
    if (location.hash.startsWith("#apps") && ais.some((a) => !a.seen && (a.configured || pending.has(a.key)))) poll = setTimeout(load, 3000);
  }
  window.MNEMOS_CONNECT_LOAD = load;
  const NEXT = {
    "claude-desktop": "Quit Claude Desktop (⌘Q), open it again and send any message.",
    "claude-code": "Start a new Claude Code session and send any message.",
    "codex": "Start a new Codex session and send any message.",
    "chatgpt": "Restart the ChatGPT app, switch to Codex or Work mode and send any message.",
    "cursor": "Click Install in Cursor, then send any message there.",
  };
  const pill = (t, cls) => `<span class="pill ${cls || ""}">${esc(T(t))}</span>`;
  function card(a) {
    let head, body = "";
    if (a.seen) {
      head = pill("Connected ✓", "ok");
      body = `<p class="small muted">${esc(T("It reads and saves your memory on its own."))}</p><div class="me-actions"><button class="mbtn" data-paste="${esc(a.key)}">${esc(T("Bring what it knows"))}</button></div>`;
      if (a.key === "chatgpt") body += `<p class="small muted">${esc(T("In its Codex and Work modes. In normal chats, use copy and paste."))}</p>`;
    } else if (a.kind !== "cloud" && (a.configured || pending.has(a.key))) {
      head = pill("Waiting for its first message", "wait");
      body = `<p class="small">${esc(T(NEXT[a.key] || ""))}</p><div class="cn-wait small muted"><span class="spin"></span>${esc(T("This turns Connected ✓ by itself."))}</div>`;
    } else if (a.kind !== "cloud" && a.installed) {
      head = pill("Found on this Mac");
      body = `<div class="me-actions"><button class="mbtn primary" data-connect="${esc(a.key)}">${esc(T("Connect"))}</button></div>`;
      if (a.key === "chatgpt") body += `<p class="small muted">${esc(T("Works in its Codex and Work modes. In normal chats, use copy and paste."))}</p>`;
    } else {
      head = pill("Copy and paste");
      const url = remote && remote.enabled && remote.url;
      body = `<div class="me-actions"><button class="mbtn primary" data-paste="${esc(a.key)}">${esc(T("Bring what it knows"))}</button></div>`
        + (url ? `<details class="small"><summary class="muted">${esc(T("Live connection"))}</summary><ol class="small cn-steps">${(STEPS[a.key] || []).map((x) => `<li>${esc(T(x))}</li>`).join("")}</ol><div class="cn-url"><code>${esc(url)}</code><button class="mbtn" data-cncopy="${esc(url)}">${esc(T("Copy address"))}</button></div></details>`
          : `<p class="small muted">${esc(T("A live connection needs Reach from anywhere (Settings → Advanced)."))}</p>`);
    }
    return `<div class="cn-card${a.seen ? " on" : ""}" data-k="${esc(a.key)}"><div class="cn-head"><b>${esc(T(a.name))}</b>${head}</div>${body}</div>`;
  }
  // which cards to show: everything found, connected or in the cloud; apps not on this Mac fold away
  const shown = (a) => a.seen || a.configured || a.installed || a.kind === "cloud" || pending.has(a.key);
  const STEPS = {
    "chatgpt": ["In ChatGPT: Settings → Apps → Advanced → turn on Developer mode (business plans).", "Add a custom server, paste the address below and choose OAuth.", "A Mnemos page opens: type your Mnemos password and pick the spaces."],
    "claude-web": ["In Claude: Settings → Connectors → Add custom connector.", "Paste the address below and press Connect.", "A Mnemos page opens: type your Mnemos password and pick the spaces."],
    "grok": ["In Grok: Connectors → New Connector → Custom.", "Paste the address below.", "A Mnemos page opens: type your Mnemos password and pick the spaces."],
  };
  function render() {
    // desktop apps first (connected, then found), copy-and-paste ones last
    const rank = (a) => (a.seen ? 0 : a.kind !== "cloud" && (a.configured || a.installed) ? 1 : 2);
    const vis = ais.filter(shown).sort((x, y) => rank(x) - rank(y)), rest = ais.filter((a) => !shown(a));
    $("cn-grid").innerHTML = vis.map(card).join("")
      + (rest.length ? `<details class="cn-missing small"><summary class="muted">${esc(T("Not on this Mac:"))} ${rest.map((a) => esc(T(a.name))).join(", ")}</summary><p class="small muted">${esc(T("Install one and it shows up here by itself."))}</p></details>` : "");
  }
  async function connect(key, btn) {
    btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${esc(T("Connecting…"))}`; $("cn-msg").textContent = "";
    try {
      const r = await window.MNEMOS_API("/api/connect", { key });
      pending.add(key);
      if (r.open) location.href = r.open;  // Cursor's own install link: Cursor asks to confirm
    } catch (e) { $("cn-msg").textContent = T(e.message); $("cn-msg").className = "small err"; }
    load();
  }
  function copy(text) {
    window.MnemosCoach.start({ text, steps: [{ text: T("Open the AI's connector settings (steps above).") }, { text: T("Paste the address (⌘V) and connect.") }, { text: T("Type your Mnemos password on the page that opens.") }] });
  }
  function bring(key) {
    const a = ais.find((x) => x.key === key); if (!a) return;
    $("bk-title").textContent = T("Bring what {x} knows").replace("{x}", T(a.name));
    $("bk-modal").classList.remove("hidden");
    window.MnemosBring.open(a, $("bk-body"), { onDone: () => { closeBring(); load(); } });
  }
  function closeBring() { $("bk-modal").classList.add("hidden"); window.MnemosCoach.stop(); }
  $("bk-close").onclick = closeBring;
  $("bk-modal").addEventListener("click", (e) => { if (e.target.id === "bk-modal") closeBring(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("bk-modal").classList.contains("hidden")) closeBring(); });
  document.addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-cncopy]"); if (c) { ev.preventDefault(); copy(c.dataset.cncopy); }
    const k = ev.target.closest("[data-connect]"); if (k) { ev.preventDefault(); connect(k.dataset.connect, k); }
    const pz = ev.target.closest("[data-paste]");
    if (pz) { ev.preventDefault(); bring(pz.dataset.paste); }
  });
  window.MNEMOS_BOOTSTRAP = boot;
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "apps") load(); else clearTimeout(poll); });
})();
