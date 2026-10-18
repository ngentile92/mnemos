"use strict";
// Reach from anywhere: opt-in Tailscale Funnel so cloud AI apps can use this computer's Mnemos (OAuth sign-in).
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const link = (u, t) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(t)}</a>`;
  let st = null;
  function msg(t, bad) { $("rm-msg").innerHTML = t; $("rm-msg").style.color = bad ? "var(--bad,#f87171)" : "var(--ok,#34d399)"; }
  async function load(check) {
    try { st = await window.MNEMOS_API("/api/remote" + (check ? "?check=1" : "")); } catch (e) { return msg(esc(e.message), true); }
    render();
  }
  function tsHint(ts) {
    if (!ts) return "";
    if (!ts.installed) return `<p class="small">1. Install ${link("https://tailscale.com/download", "Tailscale")} (free) and sign in. 2. Come back and turn this on.</p>`;
    if (!ts.running) return '<p class="small">Tailscale is installed but not signed in or not running. Open Tailscale, sign in, then turn this on.</p>';
    return `<p class="small muted">Tailscale is ready on <code>${esc(ts.dns_name)}</code>.</p>`;
  }
  function render() {
    $("rm-state").textContent = st.enabled ? "on" : "off";
    if (st.enabled) {
      const warn = st.funnel_on === false ? '<p class="small rm-warn">Tailscale says Funnel is off for this address. Turn this off and on again.</p>' : "";
      $("rm-body").innerHTML = `<p>Address for cloud apps: <code id="rm-url">${esc(st.url)}</code> <button class="mbtn" id="rm-copy">Copy</button></p>${warn}
        <details class="small"><summary>How to add it to each app</summary><ul>
          <li><b>Claude</b> (web, desktop and phone share connectors): Settings → Connectors → Add custom connector → paste the address → Connect.</li>
          <li><b>ChatGPT</b>: Settings → Apps &amp; Connectors → Advanced → Developer mode on → Create → paste the address, authentication OAuth.</li>
          <li><b>Grok</b> and other apps with “remote MCP server”: paste the address; choose OAuth if asked.</li>
          <li>A Mnemos page opens: type your Mnemos password and tick the spaces that app may use. It then appears in the list above, marked “from anywhere”.</li></ul></details>
        <label class="small"><input type="checkbox" id="rm-awake" ${st.keep_awake ? "checked" : ""}> Keep this computer from sleeping while this is on (when plugged in; closing the lid still sleeps)</label>
        <div class="ap-ctl"><button class="mbtn danger" id="rm-off">Turn off</button> <button class="mbtn" id="rm-check">Check</button></div>`;
    } else {
      const pw = st.has_password ? "" : `<p class="small">Choose a Mnemos password (at least ${st.min_password} characters). You type it when an app connects; Mnemos never sends it anywhere.</p>
        <input type="password" id="rm-pw" autocomplete="new-password" placeholder="Mnemos password" style="max-width:280px">`;
      $("rm-body").innerHTML = `${tsHint(st.tailscale)}${pw}
        <label class="small"><input type="checkbox" id="rm-awake" checked> Keep this computer from sleeping while this is on (when plugged in)</label>
        <div class="ap-ctl"><button class="mbtn primary" id="rm-on">Turn on Reach from anywhere</button></div>`;
    }
  }
  $("rm-body").addEventListener("click", async (ev) => {
    const id = ev.target.id;
    if (id === "rm-copy") { try { await navigator.clipboard.writeText(st.url); msg("Copied."); } catch { msg("Select the address and copy it.", true); } }
    if (id === "rm-check") { msg("Checking…"); await load(true); msg(st.funnel_on === false ? "Funnel is off in Tailscale." : "Working."); }
    if (id === "rm-off") {
      if (!confirm("Turn off Reach from anywhere? Cloud apps lose access right away; apps on this computer keep working.")) return;
      try { st = await window.MNEMOS_API("/api/remote", { on: false }); render(); msg(st.warning ? esc(st.warning) : "Off. Nothing is reachable from outside this computer.", !!st.warning); }
      catch (e) { msg(esc(e.message), true); }
    }
    if (id === "rm-on") {
      if (!confirm("Turn on Reach from anywhere? Your memory becomes reachable from the internet at a private address, only for apps that sign in with your Mnemos password. Your computer must stay awake for it to work.")) return;
      ev.target.disabled = true; msg("Turning on… (Tailscale may take a few seconds)");
      try {
        const r = await window.MNEMOS_API("/api/remote", { on: true, password: $("rm-pw")?.value || "", keep_awake: $("rm-awake").checked });
        if (r.ok) { st = r; render(); return msg("On. Copy the address into your cloud app."); }
        ev.target.disabled = false;
        if (r.needs === "tailnet-admin") return msg(`Tailscale needs you to allow Funnel (and HTTPS) once for your network: ${link(r.url, "open Tailscale's page")}, approve, then press Turn on again.`, true);
        if (r.url) return msg(`${esc(r.error)} ${link(r.url, "Sign in to Tailscale")}`, true);
        msg(esc(r.error || "Could not turn it on."), true);
        if (r.needs !== "password") load();
      } catch (e) { ev.target.disabled = false; msg(esc(e.message), true); }
    }
  });
  $("rm-body").addEventListener("change", async (ev) => {
    if (ev.target.id === "rm-awake" && st.enabled) {
      try { st = await window.MNEMOS_API("/api/remote", { keep_awake: ev.target.checked }); msg(ev.target.checked ? "This computer stays awake while plugged in." : "This computer may sleep; cloud apps stop working while it does."); }
      catch (e) { msg(esc(e.message), true); }
    }
  });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "apps") load(false); });
})();
