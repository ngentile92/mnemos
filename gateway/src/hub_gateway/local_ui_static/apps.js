"use strict";
// Apps screen: each AI app's allowed spaces (0.19: no default space to pick; the AI chooses where each note goes), pause / remove access, and the activity feed.
(() => {
  // activity summaries: "verb “quoted text” → n notes" — translate the verb and the tail, never the quoted text
  const trSum = (s) => { const T = window.tr || ((x) => x); const m = String(s || "").match(/^([^“]*)(“.*”)?(.*)$/s); return m ? [T(m[1].trim()), m[2] || "", m[3].trim() ? T(m[3].trim()) : ""].filter(Boolean).join(" ") : s; };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let data = null;
  const label = (n) => ((window.MNEMOS_SPACES || {}).spaces || []).find((s) => s.name === n)?.label || n;
  const when = (ts) => { if (!ts) return "never"; const d = new Date(ts); return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); };
  function msg(t, bad) { $("ap-msg").textContent = t; $("ap-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  async function load() {
    try { data = await window.MNEMOS_API("/api/apps"); } catch (e) { return msg(e.message, true); }
    render(); feed();
  }
  function render() {
    $("ap-head").textContent = `${data.apps.length} app${data.apps.length === 1 ? "" : "s"}`;
    $("ap-filter").innerHTML = '<option value="">All apps</option>' + data.apps.map((a) => `<option value="${esc(a.key)}">${esc(a.label)}</option>`).join("");
    if (!data.apps.length) {
      $("ap-list").innerHTML = '<p class="small muted">No AI app has used your memory yet. Connect one above.</p>';
      return;
    }
    $("ap-list").innerHTML = data.apps.map((a, i) => {
      const st = a.stats || {};
      const state = a.revoked ? "revoked" : a.paused ? "paused" : "";
      return `<div class="ap-row ${state}" data-i="${i}"><div class="ap-top"><div><b>${esc(a.label)}</b>
          <span class="ap-pill">${a.via === "remote" ? "from anywhere" : "this Mac"}</span>
          ${a.revoked ? '<span class="ap-pill">access removed</span>' : a.paused ? '<span class="ap-pill">paused</span>' : ""}</div>
          <span class="small muted">last used ${esc(when(a.last_seen))} · 7 days: ${st.read || 0} reads, ${st.write || 0} saves${st.denied ? `, ${st.denied} refused` : ""}</span></div>
        <div class="ap-ctl small">
          ${data.spaces.filter((s) => s !== "shared").length > 1 ? `<span>May use:</span>${data.spaces.map((s) => `<label><input type="checkbox" class="ap-sp" value="${esc(s)}" ${a.allowed.includes(s) ? "checked" : ""}> ${esc(label(s))}</label>`).join("")}
          <span class="muted">(Shared: always readable)</span>` : ""}
        </div>
        <div class="ap-ctl"><button class="mbtn ap-pause">${a.paused ? "Resume" : "Pause"}</button>
          <button class="mbtn ${a.revoked ? "primary" : "danger"} ap-revoke">${a.revoked ? "Allow again" : "Remove access"}</button>
          <button class="mbtn ap-see">See activity</button></div></div>`;
    }).join("");
  }
  async function update(body, ok) {
    try { data = await window.MNEMOS_API("/api/apps/update", body); render(); msg(ok); feed(); } catch (e) { msg(e.message, true); }
  }
  $("ap-list").addEventListener("change", (ev) => {
    const row = ev.target.closest(".ap-row"); if (!row) return;
    const a = data.apps[+row.dataset.i];
    if (ev.target.classList.contains("ap-sp")) {
      const spaces = [...row.querySelectorAll(".ap-sp:checked")].map((x) => x.value);
      if (!spaces.length) { ev.target.checked = true; return msg("Leave at least one space, or use Pause.", true); }
      update({ key: a.key, spaces }, `${a.label} may use: ${spaces.map(label).join(", ")}.`);
    }
  });
  $("ap-list").addEventListener("click", (ev) => {
    const row = ev.target.closest(".ap-row"); if (!row) return;
    const a = data.apps[+row.dataset.i];
    if (ev.target.classList.contains("ap-pause")) update({ key: a.key, paused: !a.paused }, a.paused ? `${a.label} resumed.` : `${a.label} paused: it cannot read or save until you resume it.`);
    if (ev.target.classList.contains("ap-revoke")) {
      if (!a.revoked && !confirm(`Remove ${a.label}'s access to your memory? Your notes stay. You can allow it again later.`)) return;
      update({ key: a.key, revoked: !a.revoked }, a.revoked ? `${a.label} can use Mnemos again.` : `${a.label} has no access now. To remove Mnemos from it completely, delete it in that app's settings too.`);
    }
    if (ev.target.classList.contains("ap-see")) { $("ap-filter").value = a.key; feed(); $("ap-feed").scrollIntoView({ behavior: "smooth" }); }
  });
  async function feed() {
    let r;
    try { r = await window.MNEMOS_API("/api/activity?" + new URLSearchParams({ app: $("ap-filter").value, limit: 150 })); }
    catch (e) { $("ap-feed").innerHTML = `<p class="small muted">${esc(e.message)}</p>`; return; }
    $("ap-feed").innerHTML = r.events.length ? r.events.map((e) => `<div class="ap-ev"><span class="muted">${esc(when(e.ts))}</span>
        <b>${esc(e.label)}</b><span class="k-${esc(e.kind)}">${esc(trSum(e.summary))}${e.space ? ` <span class="ap-pill">${esc(label(e.space))}</span>` : ""}</span></div>`).join("")
      : '<p class="small muted">Nothing yet. What your AI apps read or save shows up here.</p>';
  }
  $("ap-filter").onchange = feed;
  $("ap-refresh").onclick = load;
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "apps") load(); });
})();
