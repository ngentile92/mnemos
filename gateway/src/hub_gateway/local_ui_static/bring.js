"use strict";
// Bring what an AI knows (v0.18), one decision per screen:
//   1. found on this Mac (its files, or its export in Downloads): one button saves it, with Undo;
//   2. otherwise ask the AI: connected → it saves its notes itself; not connected → copy, paste its answer back;
//      an export .zip can be dropped instead (Claude's includes its saved memory; ChatGPT's only chats).
// Asking is optional when Mnemos already has something from that AI and required when it has nothing: in the first
// run that step only finishes when notes from that AI arrive.
(() => {
  const T = (s) => (window.tr || ((x) => x))(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p, b) => window.MNEMOS_API(p, b);
  const es = () => /^es/i.test(document.documentElement.lang || navigator.language || "");
  const ASK = {
    en: "List every memory you have stored about me and everything you have learned about me from our past conversations. One fact per line, in a single code block, in my own words where possible: who I am, my work and projects, the people who matter to me, my preferences, the tools I use, my goals and the instructions I gave you about how to answer. No commentary, and no passwords, keys or private numbers.",
    es: "Listá todo lo que tenés guardado sobre mí y todo lo que aprendiste de mí en nuestras conversaciones anteriores. Un dato por línea, en un solo bloque de código, con mis palabras cuando se pueda: quién soy, mi trabajo y proyectos, las personas importantes para mí, mis preferencias, las herramientas que uso, mis objetivos y las instrucciones que te di sobre cómo responder. Sin comentarios, y sin contraseñas, claves ni números privados.",
  };
  const ask = () => ASK[es() ? "es" : "en"];
  const SRC = { "claude-desktop": "claude", "claude-web": "claude", chatgpt: "chatgpt", grok: "grok" };
  const KIND_APP = { claude_code: "claude-code", claude_memory: "claude-code", claude_md: "claude-code", cursor_chats: "cursor", cursor_rules: "cursor",
    codex_memory: "codex", agents_md: "codex", chatgpt_zip: "chatgpt", claude_zip: "claude-desktop" };
  const OPEN = { chatgpt: "https://chatgpt.com/", "claude-web": "https://claude.ai/new", grok: "https://grok.com/" };
  const EXPORT = { "claude-desktop": 1, "claude-web": 1, chatgpt: 1 };
  async function knownOf(key) { try { return ((await api("/api/ai/known")).known || {})[key] || 0; } catch (_) { return 0; } }
  async function ctx() { try { return (await api("/api/session")).contexts.find((c) => c !== "shared") || "personal"; } catch (_) { return "personal"; } }

  // ai: {key, name, seen}; box: element to render into; opts: {forced, onDone(n)}
  async function open(ai, box, opts = {}) {
    const name = T(ai.name);
    const base = await knownOf(ai.key);
    const forced = opts.forced && base === 0;
    const finish = (n) => { window.MnemosCoach && window.MnemosCoach.stop(); if (opts.onDone) opts.onDone(n); };
    const skip = () => (forced ? "" : `<button class="mbtn ghost" data-bk="skip">${esc(base ? T("Skip: Mnemos already has notes from it") : T("Skip"))}</button>`);
    // 1. its own files on this Mac, or its export in Downloads
    box.innerHTML = `<div class="wl-row"><span class="spin"></span>${esc(T("Looking on this computer…"))}</div>`;
    let found = [];
    try { found = ((await api("/api/discover/scan")).sources || []).filter((s) => KIND_APP[s.kind] === ai.key || (ai.key === "claude-web" && s.kind === "claude_zip")); } catch (_) { found = []; }
    const todo = found.filter((s) => s.status !== "imported");
    if (todo.length) {
      const n = todo.reduce((a, s) => a + (s.count || 0), 0);
      box.innerHTML = `<p class="bk-say">${esc(T("Mnemos found what {x} keeps on this Mac.").replace("{x}", name))}</p>
        <p class="small muted">${todo.map((s) => esc(s.label)).join(" · ")} · ${n}</p>
        <div class="me-actions"><button class="mbtn primary" data-bk="auto">${esc(T("Save what it knows"))}</button>${skip()}</div><p class="small" id="bk-msg"></p>`;
      box.querySelector('[data-bk="auto"]').onclick = (e) => auto(e.target, todo);
      wireSkip();
      return;
    }
    askScreen();

    function wireSkip() { const s = box.querySelector('[data-bk="skip"]'); if (s) s.onclick = () => finish(0); }
    async function auto(btn, srcs) {
      btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${esc(T("Reading…"))}`;
      const msg = box.querySelector("#bk-msg");
      try {
        await api("/api/discover/preview", { sources: srcs.map((x) => ({ id: x.id, context: x.context || "auto" })) });
        let j; for (;;) { await new Promise((r) => setTimeout(r, 900)); j = await api("/api/discover/status"); if (j.state !== "running") break; }
        if (j.state === "error") throw new Error(j.error);
        const notes = (j.result.items || []).filter((it) => !it.duplicate).map((it) => ({ text: it.text, context: it.context, source: it.source, source_id: it.source_id }));
        const r = notes.length ? await api("/api/discover/commit", { notes }) : { saved: 0 };
        if (!r.saved) { askScreen(T("Nothing new in those files.")); return; }
        box.innerHTML = `<p class="bk-say ok">${esc(T("Saved {n} ✓").replace("{n}", r.saved))}</p><div class="me-actions"><button class="mbtn primary" data-bk="next">${esc(T("Continue"))}</button>${r.batch ? `<button class="mbtn ghost" data-bk="undo">${esc(T("Undo"))}</button>` : ""}</div>`;
        box.querySelector('[data-bk="next"]').onclick = () => finish(r.saved);
        const u = box.querySelector('[data-bk="undo"]');
        if (u) u.onclick = async () => { try { await api("/api/discover/undo", { batch: r.batch }); } catch (_) { /* shown below */ } open(ai, box, opts); };
      } catch (err) { btn.disabled = false; btn.textContent = T("Try again"); if (msg) { msg.textContent = T(err.message); msg.className = "small err"; } }
    }
    // 2. ask the AI
    function askScreen(lead) {
      const live = !!ai.seen && ai.kind !== "cloud";
      const say = live ? T("Ask {x} to save what it knows about you.") : T("Ask {x} what it knows, then paste its answer here.");
      box.innerHTML = `${lead ? `<p class="small muted">${esc(lead)}</p>` : ""}<p class="bk-say">${esc(say.replace("{x}", name))}</p>
        <div class="me-actions"><button class="mbtn primary" data-bk="copy">${esc(T(live ? "Copy the request" : "Copy the question"))}</button>${skip()}</div>
        ${live ? `<div class="bk-wait hidden" id="bk-wait"><span class="spin"></span><span>${esc(T("Waiting for its notes…"))}</span></div>`
          : `<textarea id="bk-paste" class="bk-paste" placeholder="${esc(T("Paste the answer here"))}"></textarea><div id="bk-review"></div>`}
        ${EXPORT[ai.key] ? `<label class="bk-drop small" id="bk-drop"><input type="file" id="bk-file" accept=".zip,.json" hidden><span>${esc(T(ai.key === "chatgpt" ? "Or drop your ChatGPT export (.zip): it has your chats, not its saved memory." : "Or drop your Claude export (.zip): it includes Claude's saved memory."))}</span></label>` : ""}
        ${forced ? `<p class="small muted">${esc(T("This step finishes when its notes arrive."))}</p>` : ""}`;
      wireSkip();
      box.querySelector('[data-bk="copy"]').onclick = () => {
        if (live) {
          const req = window.MNEMOS_BOOTSTRAP ? window.MNEMOS_BOOTSTRAP() : ask();
          window.MnemosCoach.start({ text: req, away: 1,
            steps: [{ text: T("Open {x} and start a new chat.").replace("{x}", name), open: OPEN[ai.key], openLabel: T("Open {x}").replace("{x}", name) },
                    { text: T("Paste (⌘V) and send.") }, { text: T("{x} saves each thing here by itself.").replace("{x}", name) }],
            done: async () => { const k = await knownOf(ai.key); return k > base ? T("{n} notes from {x} arrived ✓").replace("{n}", k - base).replace("{x}", name) : false; },
            onDone: () => setTimeout(() => finish((0)), 1500) });
          box.querySelector("#bk-wait").classList.remove("hidden");
        } else {
          window.MnemosCoach.start({ text: ask(), away: 1, target: "bk-paste",
            steps: [{ text: T("Open {x} and start a new chat.").replace("{x}", name), open: OPEN[ai.key], openLabel: T("Open {x}").replace("{x}", name) },
                    { text: T("Paste (⌘V) and send.") }, { text: T("Copy its whole answer.") }, { text: T("Paste it here (⌘V), anywhere on this page.") }],
            onPaste: (t) => review(t) });
        }
      };
      const ta = box.querySelector("#bk-paste");
      if (ta) {
        ta.addEventListener("paste", () => setTimeout(() => review(ta.value), 50));
        let typed; ta.addEventListener("input", () => { clearTimeout(typed); typed = setTimeout(() => review(ta.value), 700); });  // typed or dropped text too
      }
      const f = box.querySelector("#bk-file"), drop = box.querySelector("#bk-drop");
      if (f) {
        f.onchange = () => f.files[0] && upload(f.files[0]);
        drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
        drop.addEventListener("dragleave", () => drop.classList.remove("over"));
        drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); const x = e.dataTransfer.files[0]; if (x) upload(x); });
      }
    }
    let rev = null, c = null;
    async function review(text) {
      if (!text || text.trim().length < 8) return;
      c = c || await ctx();
      try { rev = await api("/api/import/preview", { context: c, source: SRC[ai.key] || "text", text }); } catch (e) { return showErr(e.message); }
      showReview();
    }
    async function upload(file) {
      c = c || await ctx();
      const out = box.querySelector("#bk-review") || box;
      out.innerHTML = `<div class="wl-row"><span class="spin"></span>${esc(T("Reading the export…"))}</div>`;
      try {
        const r = await fetch("/api/import/upload?" + new URLSearchParams({ context: c }), { method: "POST", headers: { "X-Mnemos-Write": "1" }, body: file });
        const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
        rev = j; showReview();
      } catch (e) { showErr(e.message); }
    }
    function showErr(m) { const out = box.querySelector("#bk-review"); if (out) out.innerHTML = `<p class="small err">${esc(T(m))}</p>`; }
    function showReview() {
      window.MnemosCoach && window.MnemosCoach.stop();
      const out = box.querySelector("#bk-review") || box;
      const n = rev.new || 0;
      if (!n) { out.innerHTML = `<p class="small muted">${esc(T("Nothing new in that answer."))}</p>`; return; }
      out.innerHTML = `<p class="bk-say">${esc(T("{n} new things about you.").replace("{n}", n))}</p>
        <ul class="bk-list small">${rev.items.filter((i) => !i.duplicate).slice(0, 5).map((i) => `<li>${esc(i.text)}</li>`).join("")}${n > 5 ? `<li class="muted">${esc(T("…and {n} more").replace("{n}", n - 5))}</li>` : ""}</ul>
        <div class="me-actions"><button class="mbtn primary" data-bk="save">${esc(T("Save {n}").replace("{n}", n))}</button></div>`;
      out.querySelector('[data-bk="save"]').onclick = async (e) => {
        e.target.disabled = true;
        try {
          const notes = rev.items.filter((i) => !i.duplicate).map((i) => i.text);
          const r = await api("/api/import/commit", { context: c, source: rev.source || SRC[ai.key] || "text", notes });
          out.innerHTML = `<p class="bk-say ok">${esc(T("Saved {n} ✓").replace("{n}", r.saved))}</p>`;
          setTimeout(() => finish(r.saved), 900);
        } catch (err) { e.target.disabled = false; showErr(err.message); }
      };
    }
  }
  window.MnemosBring = { open, ask };
})();
