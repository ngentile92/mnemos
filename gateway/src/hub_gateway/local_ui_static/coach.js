"use strict";
// Copy coach (v0.18): after every copy, a fixed notice says exactly what to do next and where to paste, step by step.
//   MnemosCoach.start({ title, steps: [{ text, open? }], target?, done? })   steps advance by themselves:
//   leaving the page ticks "open / paste there", coming back lights "paste it here", a paste anywhere on the page
//   lands in the target (it pulses violet), and `done` (a poll) finishes it, e.g. "the AI's notes arrived".
// Clipboard reading is only tried when the browser already allows it (never a permission prompt).
(() => {
  const T = (s) => (window.tr || ((x) => x))(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let cur = null, timer = null;
  const box = document.createElement("aside");
  box.id = "coach"; box.className = "coach hidden"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite");
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(box));

  function render() {
    if (!cur) { box.classList.add("hidden"); return; }
    const n = cur.at;
    box.innerHTML = `<div class="coach-h"><b>${esc(T(cur.title || "Copied ✓"))}</b><button class="coach-x" aria-label="${esc(T("Close"))}">×</button></div>
      <ol class="coach-steps">${cur.steps.map((s, i) => `<li class="${i < n ? "done" : i === n ? "now" : ""}"><span class="coach-n">${i < n ? "✓" : i + 1}</span><span>${esc(T(s.text))}${s.open && i === n ? ` <a class="mbtn coach-open" href="${esc(s.open)}" target="_blank" rel="noopener noreferrer">${esc(T(s.openLabel || "Open"))}</a>` : ""}</span></li>`).join("")}</ol>
      ${cur.note ? `<p class="coach-note small">${esc(T(cur.note))}</p>` : ""}
      ${cur.clip ? `<div class="me-actions"><button class="mbtn primary coach-clip">${esc(T("Paste what you copied"))}</button></div>` : ""}`;
    box.classList.remove("hidden");
    box.querySelector(".coach-x").onclick = stop;
    const c = box.querySelector(".coach-clip"); if (c) c.onclick = () => land(cur.clip);
  }
  function stepTo(i) { if (!cur) return; cur.at = Math.max(cur.at, Math.min(i, cur.steps.length)); render(); }
  function pulse(on) { const t = cur && cur.target && document.getElementById(cur.target); if (t) t.classList.toggle("paste-pulse", on); }
  function land(text) {
    if (!cur || !cur.target) return;
    const t = document.getElementById(cur.target); if (!t) return;
    t.value = text; t.dispatchEvent(new Event("input", { bubbles: true })); cur.clip = null;
    stepTo(cur.steps.length - 1); pulse(false);
    if (cur.onPaste) cur.onPaste(text);
  }
  function stop() { clearTimeout(timer); pulse(false); cur = null; render(); }
  async function tick() {
    if (!cur) return;
    if (cur.done) {
      let r = false; try { r = await cur.done(); } catch (_) { /* keep waiting */ }
      if (r) { cur.at = cur.steps.length; cur.title = typeof r === "string" ? r : cur.doneTitle || "Done ✓"; cur.note = ""; render(); pulse(false);
        const fin = cur.onDone; setTimeout(() => { if (cur && cur.at >= cur.steps.length) stop(); }, 4000); if (fin) fin(r); return; }
    }
    timer = setTimeout(tick, 3000);
  }
  // left the page (to the AI app): the "open / paste there" steps are under way
  window.addEventListener("blur", () => { if (cur && cur.at < cur.away) stepTo(cur.away); });
  // back: light the paste target, and offer the clipboard when the browser already allows reading it
  window.addEventListener("focus", async () => {
    if (!cur) return;
    if (cur.target) {
      stepTo(cur.steps.length - 1); pulse(true);
      const t = document.getElementById(cur.target); if (t) t.scrollIntoView({ block: "center", behavior: "smooth" });
      try {
        const p = await navigator.permissions.query({ name: "clipboard-read" });
        if (p.state === "granted") { const x = await navigator.clipboard.readText(); if (x && x.trim() !== cur.copied.trim() && x.length > 30) { cur.clip = x; render(); } }
      } catch (_) { /* not allowed: the paste box is enough */ }
    }
  });
  // a paste anywhere on the page (not in another field) goes to the target
  document.addEventListener("paste", (ev) => {
    if (!cur || !cur.target) return;
    const el = ev.target; if (el && el.id !== cur.target && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
    const text = (ev.clipboardData || window.clipboardData).getData("text");
    if (!text) return; ev.preventDefault(); land(text);
  });
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) {
      const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand("copy"); } catch (_) { ok = false; } ta.remove(); return ok;
    }
  }
  window.MnemosCoach = {
    // opts: { text, title, steps, away (index of the first step done in the other app), target, onPaste, done, onDone, note }
    async start(opts) {
      stop();
      const ok = await copy(opts.text);
      cur = { at: 0, away: opts.away ?? 1, copied: opts.text, ...opts, title: ok ? (opts.title || "Copied ✓") : "Select the text and copy it" };
      render(); if (cur.target) pulse(true); tick();
      return ok;
    },
    stop, step: stepTo, active: () => !!cur, copy,
  };
})();
