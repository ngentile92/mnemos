"use strict";
// "Connected apps": grants of the single connector (hub-router). Hidden when the router is not enabled.
(() => {
  const card = document.getElementById("apps-card"), box = document.getElementById("apps");
  if (!card) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  async function load() {
    let r;
    try { r = await fetch("/api/router/grants"); } catch { return; }
    if (r.status === 409) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { box.innerHTML = `<p class="muted">${esc(j.error || "HTTP " + r.status)}</p>`; return; }
    const rows = j.grants || [];
    const lock = j.locked_seconds > 0 ? `<p class="small"><b>Sign-in locked</b> for ${Math.ceil(j.locked_seconds / 60)} more min after too many failed attempts. <button class="btn" data-unlock="1">Unlock now</button></p>` : "";
    if (!rows.length) { box.innerHTML = lock + '<p class="muted">No app connected yet.</p>'; return; }
    box.innerHTML = lock + "<table><thead><tr><th>App</th><th>Signed in as</th><th>Contexts</th><th>Switch</th><th>Since</th><th></th></tr></thead><tbody>" +
      rows.map((g) => `<tr><td>${esc(g.client_name || g.client_id)}</td><td>${esc(g.login)}</td><td>${(g.contexts || []).map((c) => `<span class="tag">${esc(c)}</span>`).join(" ")}</td>` +
        `<td>${g.switch ? "yes" : "no"}</td><td>${g.created ? new Date(g.created * 1000).toLocaleString() : ""}</td>` +
        `<td><button class="btn" data-revoke="${esc(g.client_id)}">Revoke</button></td></tr>`).join("") + "</tbody></table>";
  }
  box.addEventListener("click", async (ev) => {
    if (ev.target?.dataset?.unlock) {
      const r = await fetch("/api/router/unlock", { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" }, body: "{}" });
      if (!r.ok) alert((await r.json().catch(() => ({}))).error || "HTTP " + r.status);
      return load();
    }
    const id = ev.target?.dataset?.revoke;
    if (!id || !confirm("Revoke this app? Its tokens stop working now; it will ask to sign in again.")) return;
    const r = await fetch("/api/router/revoke", { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" },
      body: JSON.stringify({ client_id: id }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) alert(j.error || "HTTP " + r.status);
    load();
  });
  load();
  setInterval(load, 60000);
})();
