"use strict";
// First run (v0.18): one loop per AI, then your map.
//   name (only if the Mnemos window did not ask) → which AIs (the ones on this Mac pre-marked) → for each AI:
//   connect (desktop apps; ticks itself on the app's first call) → bring what it knows (required when Mnemos has
//   nothing from it) → use Mnemos every time (automatic for coding apps; one pasted instruction for chat apps)
//   → next AI → the map reveal. One decision per screen, at most ~25 words.
// Progress is kept in localStorage, so a reload mid-way comes back to the same AI.
(() => {
  const $ = (id) => document.getElementById(id);
  const T = (s) => (window.tr || ((x) => x))(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p, b) => window.MNEMOS_API(p, b);
  const fill = (s, x) => T(s).replace("{x}", x);
  const KEY = "mnemosOnboarding";
  const es = () => /^es/i.test(document.documentElement.lang || navigator.language || "");
  const CODING = { "claude-code": 1, codex: 1, cursor: 1 };
  const WHERE = {
    "claude-desktop": "In Claude: Settings → Account → Instructions for Claude.",
    "claude-web": "In Claude: Settings → Account → Instructions for Claude.",
    chatgpt: "In ChatGPT: Settings → Personalization → Custom instructions.",
    grok: "In Grok: Settings → Customize → Custom instructions.",
  };
  const OPEN = { chatgpt: "https://chatgpt.com/", "claude-web": "https://claude.ai/new", grok: "https://grok.com/" };
  const USE = {
    en: "I use Mnemos as my long-term memory. At the start of every chat, check it for what you know about me. When I share something lasting about myself, save it there right away in one short sentence, without asking.",
    es: "Uso Mnemos como mi memoria de largo plazo. Al empezar cada chat, fijate ahí qué sabés de mí. Cuando cuente algo duradero sobre mí, guardalo ahí enseguida en una frase corta, sin preguntar.",
  };
  const save = (st) => localStorage.setItem(KEY, JSON.stringify(st));
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (_) { return null; } };
  const mark = (step) => api("/api/ftue", { step }).catch(() => {});
  let st = null, ais = [], poll = null, finishCb = null;

  async function cards() { try { ais = (await api("/api/connect")).ais || []; } catch (_) { ais = []; } return ais; }
  const ai = (k) => ais.find((a) => a.key === k) || { key: k, name: k, kind: "cloud" };
  function pane(n) { clearTimeout(poll); for (let i = 1; i <= 3; i++) $("wl-p" + i).classList.toggle("hidden", i !== n); }
  function dots() {
    const list = st && st.picked ? st.picked : [];
    $("wl-steps").innerHTML = list.map((k, i) => `<li class="${i < st.i ? "done" : i === st.i ? "on" : ""}">${esc(T(ai(k).name))}</li>`).join("")
      + (list.length ? `<li class="${st.i >= list.length ? "on" : ""}">${esc(T("Your map"))}</li>` : "");
  }

  async function start(session, welcome, onFinish) {
    finishCb = onFinish;
    $("welcome").classList.remove("hidden");
    st = load() || { phase: welcome.name ? "pick" : "name", picked: [], i: 0, sub: "connect" };
    save(st); mark("welcome");
    await cards();
    if (st.phase === "name") return nameStep(welcome);
    if (st.phase === "pick") return pickStep();
    return loop();
  }
  function nameStep(w) {
    pane(1); dots();
    $("wl-name").value = w.name || ""; $("wl-name").focus();
    const one = (w.spaces || []).length === 1;  // 0.19: a new memory is one space; name it here (optional)
    $("wl-mem-row").classList.toggle("hidden", !one);
    if (one && w.spaces[0].label !== "My memory") $("wl-memory").value = w.spaces[0].label;
    const go = async () => {
      $("wl-go").disabled = true; $("wl-err").textContent = "";
      try { await api("/api/welcome", { name: $("wl-name").value, memory: one ? $("wl-memory").value : "" }); st.phase = "pick"; save(st); pickStep(); }
      catch (e) { $("wl-err").textContent = T(e.message); }
      $("wl-go").disabled = false;
    };
    $("wl-go").onclick = go; $("wl-name").onkeydown = $("wl-memory").onkeydown = (e) => { if (e.key === "Enter") go(); };
  }
  function pickStep() {
    pane(2); dots();
    const pre = new Set(ais.filter((a) => a.installed || a.configured || a.seen).map((a) => a.key));
    $("wl-pick").innerHTML = ais.map((a) => `<button type="button" class="wl-chip${pre.has(a.key) ? " on" : ""}" data-k="${esc(a.key)}" aria-pressed="${pre.has(a.key)}">
      <span class="wl-check">✓</span>${esc(T(a.name))}${a.installed && a.kind !== "cloud" ? `<span class="wl-here">${esc(T("on this Mac"))}</span>` : ""}</button>`).join("");
    $("wl-pick").onclick = (e) => { const b = e.target.closest(".wl-chip"); if (!b) return; const on = !b.classList.contains("on"); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); upd(); };
    const upd = () => { const n = $("wl-pick").querySelectorAll(".wl-chip.on").length; $("wl-go2").disabled = !n; $("wl-go2").textContent = n ? T("Continue") : T("Pick at least one"); };
    upd();
    $("wl-go2").onclick = () => {
      const rank = (k) => (ai(k).kind !== "cloud" && ai(k).installed ? 0 : 1);  // desktop apps first
      st.picked = [...$("wl-pick").querySelectorAll(".wl-chip.on")].map((b) => b.dataset.k).sort((x, y) => rank(x) - rank(y));
      st.phase = "loop"; st.i = 0; st.sub = "connect"; save(st); loop();
    };
  }
  async function loop() {
    pane(3); await cards(); dots();
    if (st.i >= st.picked.length) return reveal();
    const a = ai(st.picked[st.i]);
    $("wl-ai").textContent = `${st.i + 1} / ${st.picked.length} · ${T(a.name)}`;
    if (st.sub === "connect") return connectStep(a);
    if (st.sub === "bring") return bringStep(a);
    return defaultStep(a);
  }
  const next = (sub) => { if (sub) st.sub = sub; else { st.i += 1; st.sub = "connect"; } save(st); loop(); };

  // 1. connect: desktop and coding apps only; cloud ones go straight to copy and paste
  function connectStep(a) {
    const can = a.kind !== "cloud" && a.installed;
    if (a.seen || !can) return next("bring");
    $("wl-h3").textContent = fill("Connect {x}", T(a.name));
    const waiting = a.configured;
    $("wl-body").innerHTML = waiting ? waitHtml(a) : `<p class="muted">${esc(fill("One click adds Mnemos to {x}. Nothing else changes.", T(a.name)))}</p>
      <div class="me-actions"><button class="mbtn primary" id="wl-connect">${esc(fill("Connect {x}", T(a.name)))}</button></div><p class="small" id="wl-cmsg"></p>`;
    if (waiting) return wait(a);
    $("wl-connect").onclick = async (e) => {
      e.target.disabled = true; e.target.innerHTML = `<span class="spin"></span>${esc(T("Connecting…"))}`;
      try { const r = await api("/api/connect", { key: a.key }); if (r.open) location.href = r.open; a.configured = true; $("wl-body").innerHTML = waitHtml(a); wait(a); }
      catch (err) { e.target.disabled = false; e.target.textContent = T("Try again"); $("wl-cmsg").textContent = T(err.message); $("wl-cmsg").className = "small err"; }
    };
  }
  const NEXT = {
    "claude-desktop": "Quit Claude Desktop (⌘Q), open it again and send any message.",
    "claude-code": "Start a new Claude Code session and send any message.",
    codex: "Start a new Codex session and send any message.",
    chatgpt: "Restart the ChatGPT app, switch to Codex or Work mode and send any message.",
    cursor: "Click Install in Cursor, then send any message there.",
  };
  function waitHtml(a) {
    return `<ol class="wl-todo"><li class="done"><span>✓</span>${esc(T("Mnemos is in its settings."))}</li><li class="now"><span>2</span>${esc(T(NEXT[a.key] || "Send it any message."))}</li></ol>
      <div class="wl-wait" id="wl-wait"><span class="spin"></span><span>${esc(fill("Waiting for {x}…", T(a.name)))}</span></div>
      <div class="me-actions hidden" id="wl-stuck"><span class="small muted">${esc(T("Didn't work? Make sure it was fully quit and opened again."))}</span><button class="mbtn ghost" id="wl-skipc">${esc(T("Skip: use copy and paste"))}</button></div>`;
  }
  function wait(a) {
    const t0 = Date.now();
    const tick = async () => {
      await cards();
      if (ai(a.key).seen) {
        $("wl-wait").classList.add("ok"); $("wl-wait").innerHTML = `<span>✓</span><span>${esc(fill("{x} is connected", T(a.name)))}</span>`;
        mark("first_app"); setTimeout(() => next("bring"), 1200); return;
      }
      if (Date.now() - t0 > 90000) { $("wl-stuck").classList.remove("hidden"); $("wl-skipc").onclick = () => next("bring"); }
      poll = setTimeout(tick, 3000);
    };
    tick();
  }
  // 2. bring what it knows: required when Mnemos has nothing from it
  function bringStep(a) {
    $("wl-h3").textContent = fill("Bring what {x} knows", T(a.name));
    window.MnemosBring.open(a, $("wl-body"), { forced: true, onDone: (n) => { if (n) mark("first_save"); next("default"); } });
  }
  // 3. use Mnemos every time
  async function defaultStep(a) {
    $("wl-h3").textContent = fill("Make {x} use your memory every time", T(a.name));
    if (CODING[a.key] && a.key !== "cursor" || (a.key === "cursor" && a.seen)) {
      $("wl-body").innerHTML = `<div class="wl-wait"><span class="spin"></span><span>${esc(T("Setting it up…"))}</span></div>`;
      try { await api("/api/sync", { target: a.key, on: true }); } catch (_) { /* it still works through the connection */ }
      $("wl-body").innerHTML = `<div class="wl-wait ok"><span>✓</span><span>${esc(fill("{x} starts every session knowing you.", T(a.name)))}</span></div>`;
      setTimeout(() => next(), 1400); return;
    }
    const live = a.seen && a.kind !== "cloud";
    let text = USE[es() ? "es" : "en"];
    if (!live) { try { text = (await api("/api/cloud/block")).block || text; } catch (_) { /* keep the short one */ } }
    $("wl-body").innerHTML = `<p class="muted">${esc(T(live ? "Paste one instruction in its settings so it checks your memory in every chat." : "Paste what Mnemos knows in its settings, so every chat starts knowing you."))}</p>
      <div class="me-actions"><button class="mbtn primary" id="wl-copyuse">${esc(T("Copy the instruction"))}</button><button class="mbtn ghost" id="wl-skipd">${esc(T("Skip"))}</button></div>
      <div class="me-actions hidden" id="wl-pasted-row"><button class="mbtn primary" id="wl-pasted">${esc(T("I pasted it"))}</button></div>`;
    $("wl-copyuse").onclick = () => {
      window.MnemosCoach.start({ text, away: 1, steps: [{ text: T(WHERE[a.key] || "Open its settings."), open: OPEN[a.key], openLabel: fill("Open {x}", T(a.name)) },
        { text: T("Paste (⌘V) and save.") }, { text: T("Come back and press “I pasted it”.") }] });
      $("wl-pasted-row").classList.remove("hidden");
    };
    $("wl-pasted").onclick = () => { window.MnemosCoach.stop(); next(); };
    $("wl-skipd").onclick = () => { window.MnemosCoach.stop(); next(); };
  }
  // the finale: your map, revealed
  async function reveal() {
    localStorage.removeItem(KEY);
    try { await api("/api/welcome", {}); } catch (_) { /* still show the map */ }
    mark("aha");
    $("welcome").classList.add("hidden");
    if (finishCb) finishCb("#graph?reveal=1");
  }
  window.MnemosOnboarding = { start, active: () => !!load() };
})();
