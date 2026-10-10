"use strict";
// Dashboard del Mnemos — solo lee /api/status (nombres, estados y conteos).
const REFRESH_MS = 20000;
// Contextos de config/contexts.yaml (los inyecta el servidor en /config.js).
const CFG = window.MNEMOS || { contexts: ["work", "personal", "side"].map((name) => ({ name, datasets: [name] })) };
const CTX = CFG.contexts.map((c) => c.name);
const PALETTE = ["var(--cb)", "var(--pe)", "var(--si)", "#34d399", "#fb923c", "#2dd4bf", "#e879f9"];
const COLOR = Object.fromEntries(CTX.map((c, i) => [c, PALETTE[i % PALETTE.length]]));
COLOR.shared = "var(--sh)";
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let last = null, lastAt = 0;

function ago(iso) {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "in " + dur(-s);
  return dur(s) + " ago";
}
function dur(s) {
  if (s < 60) return s + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h + " h" + (m ? " " + m + " min" : ""); }
  return Math.round(s / 86400) + " d";
}
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const tag = (cls, txt) => `<span class="tag ${cls}">${esc(txt)}</span>`;
const lvl = (b) => (b === true ? "live" : b === false ? "down" : "warn");

function containerMap(d) {
  const m = {};
  for (const c of (d.containers && d.containers.items) || []) m[c.service] = c;
  return m;
}
function ctState(c) {
  if (!c) return "down";
  if (c.state !== "running") return "down";
  if (c.health === "healthy" || c.health === null) return "live";
  return c.health === "starting" ? "warn" : "down";
}
function combine(...st) {
  if (st.every((s) => s === "live")) return "live";
  if (st.every((s) => s === "down")) return "down";
  return "warn";
}

// ------------------------------------------------------------------ KPIs
function renderKpis(d, cm) {
  const hubs = (d.funnel && d.funnel.hubs) || {};
  const up = CTX.filter((c) => hubs[c] && hubs[c].up).length;
  const items = (d.containers && d.containers.items) || [];
  const healthy = items.filter((c) => ctState(c) === "live").length;
  const inf = d.infisical && d.infisical.ok ? d.infisical.contexts : null;
  let secTotal = 0, secLive = !!inf;
  for (const c of CTX) {
    if (inf && inf[c] && inf[c].ok) secTotal += inf[c].count; else { secLive = false; secTotal += (d.static_import || {})[c] || 0; }
  }
  const ds = (d.cognee && d.cognee.datasets) || {};
  const memItems = Object.values(ds).reduce((a, x) => a + (x.items || 0), 0);
  const b = d.backup || {};
  const k = [
    { l: "Public hubs", v: `${up}<small>/3</small>`, d: up === 3 ? '<span class="ok">Funnel + OAuth active</span>' : '<span class="bad">check Funnel</span>' },
    { l: "Healthy containers", v: `${healthy}<small>/${items.length}</small>`, d: healthy === items.length ? '<span class="ok">all green</span>' : '<span class="wn">some degraded</span>' },
    { l: "Managed secrets", v: secTotal, d: secLive ? '<span class="ok">Infisical live</span>' : '<span class="wn">static import</span>' },
    { l: "Memory items", v: d.cognee && d.cognee.ok ? memItems : "—", d: `${Object.keys(ds).length} datasets` },
    { l: "Skills", v: d.skills && d.skills.ok ? d.skills.total : "—", d: d.skills && d.skills.head ? `repo @ ${esc(d.skills.head)}` : "" },
    { l: "Last backup", v: b.last_run ? ago(b.last_run).replace(" ago", "") : "—", d: b.success ? '<span class="ok">OK · encrypted</span>' : '<span class="bad">no data</span>' },
  ];
  $("kpis").innerHTML = k.map((x) => `<div class="kpi"><div class="l">${x.l}</div><div class="v">${x.v}</div><div class="d">${x.d}</div></div>`).join("");
}

// ------------------------------------------------------------------ SVG
const NODES = {
  claude: { x: 20, y: 60, w: 150, h: 56, t: "Claude", s: "MCP connector" },
  chatgpt: { x: 20, y: 160, w: 150, h: 56, t: "ChatGPT", s: "MCP connector" },
  grok: { x: 20, y: 260, w: 150, h: 56, t: "Grok", s: "MCP connector" },
  cursor: { x: 20, y: 360, w: 150, h: 56, t: "Cursor", s: "mcp.json" },
  funnel: { x: 265, y: 110, w: 165, h: 72, t: "Tailscale Funnel", s: "public HTTPS" },
  oauth: { x: 265, y: 285, w: 165, h: 72, t: "GitHub OAuth", s: "only your user" },
  cognee: { x: 840, y: 42, w: 215, h: 64, t: "Cognee · memory", s: "" },
  skills: { x: 840, y: 126, w: 215, h: 64, t: "Skills (git)", s: "" },
  infisical: { x: 840, y: 210, w: 215, h: 64, t: "Infisical · secrets", s: "" },
  vaultwarden: { x: 840, y: 294, w: 215, h: 64, t: "Vaultwarden", s: "human passwords" },
  ollama: { x: 840, y: 378, w: 215, h: 64, t: "Ollama · local LLM", s: "" },
};
// una caja por gateway, repartidas en la columna (3 contextos → y = 60 / 192 / 324, alto 86)
(() => {
  const n = CTX.length, h = Math.min(86, 350 / n - (n > 1 ? 16 : 0)), step = n > 1 ? (350 - h) / (n - 1) : 0;
  CTX.forEach((c, i) => {
    NODES["gw_" + c] = { x: 515, y: Math.round(n > 1 ? 60 + i * step : 192), w: 190, h: Math.round(h), t: "hub-" + c, s: "", big: 1 };
  });
})();
const EDGES = [
  ...["claude", "chatgpt", "grok", "cursor"].map((a) => [a, "funnel"]),
  ["funnel", "oauth", "v"],
  ...CTX.map((c) => ["oauth", "gw_" + c]),
  ...CTX.flatMap((c) => [["gw_" + c, "cognee"], ["gw_" + c, "skills"], ["gw_" + c, "infisical"]]),
  ["cognee", "ollama", "loop"],
];
function edgePath(a, b, kind) {
  const A = NODES[a], B = NODES[b];
  if (kind === "v") return `M${A.x + A.w / 2},${A.y + A.h} L${B.x + B.w / 2},${B.y}`;
  if (kind === "loop") {
    const x = A.x + A.w, y1 = A.y + A.h / 2, y2 = B.y + B.h / 2;
    return `M${x},${y1} C${x + 90},${y1} ${x + 90},${y2} ${x},${y2}`;
  }
  const x1 = A.x + A.w, y1 = A.y + A.h / 2, x2 = B.x, y2 = B.y + B.h / 2, mx = (x1 + x2) / 2;
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
}
function renderArch(d, cm) {
  const hubs = (d.funnel && d.funnel.hubs) || {};
  const st = { claude: "ext", chatgpt: "ext", grok: "ext", cursor: "ext" };
  const sub = {};
  const upN = CTX.filter((c) => hubs[c] && hubs[c].up).length;
  st.funnel = upN === 3 ? "live" : upN ? "warn" : "down";
  sub.funnel = `${upN}/3 hubs reachable`;
  const oN = CTX.filter((c) => hubs[c] && hubs[c].oauth_required).length;
  st.oauth = oN === 3 ? "live" : oN ? "warn" : "down";
  sub.oauth = oN === 3 ? "no token → 401 ✓" : `${oN}/3 require login`;
  const pol = (d.policy && d.policy.contexts) || {};
  for (const c of CTX) {
    const h = hubs[c] || {};
    st["gw_" + c] = combine(ctState(cm["gateway-" + c]), h.up ? "live" : "down");
    const lat = h.latency_ms != null ? ` · ${h.latency_ms} ms` : "";
    sub["gw_" + c] = `${(pol[c] || {}).count ?? "?"} injectable secrets${lat}`;
  }
  const ds = (d.cognee && d.cognee.datasets) || {};
  st.cognee = combine(ctState(cm.cognee), d.cognee && d.cognee.ok ? "live" : "down");
  sub.cognee = d.cognee && d.cognee.ok ? `${Object.keys(ds).length} datasets · ${Object.values(ds).reduce((a, x) => a + (x.items || 0), 0)} items` : "no response";
  st.skills = combine(ctState(cm["skills-sync"]), d.skills && d.skills.ok ? "live" : "down");
  sub.skills = d.skills && d.skills.ok ? `${d.skills.total} skill(s) · @${d.skills.head}` : "no data";
  const inf = d.infisical || {};
  const infOk = inf.ok && CTX.some((c) => inf.contexts && inf.contexts[c] && inf.contexts[c].ok);
  st.infisical = combine(ctState(cm.infisical), infOk ? "live" : "warn");
  sub.infisical = infOk ? `${CTX.reduce((a, c) => a + ((inf.contexts[c] || {}).count || 0), 0)} secrets (live)` : "count unavailable";
  st.vaultwarden = ctState(cm.vaultwarden);
  const llm = d.llm || {};
  st.ollama = llm.ollama_up ? "live" : "down";
  sub.ollama = llm.config ? `${llm.config.LLM_MODEL || "?"}${(llm.loaded || []).length ? " · loaded" : " · idle"}` : "";

  $("edges").innerHTML = EDGES.map(([a, b, k]) => {
    const s = [st[a], st[b]];
    const cls = s.includes("down") ? "down" : s.every((x) => x === "live" || x === "ext") ? "live" : "";
    return `<path class="edge ${cls}" d="${edgePath(a, b, k)}"/>`;
  }).join("");
  $("nodes").innerHTML = Object.entries(NODES).map(([id, n]) => {
    const s = sub[id] ?? n.s;
    const cy = n.y + n.h / 2;
    return `<g class="node ${st[id] || ""} ${n.big ? "big" : ""}"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="14"/>` +
      `<circle class="dot" cx="${n.x + 18}" cy="${cy - (s ? 9 : 0)}" r="5"/>` +
      `<text x="${n.x + 32}" y="${cy + (s ? -4 : 5)}">${esc(n.t)}</text>` +
      (s ? `<text class="nsub" x="${n.x + 32}" y="${cy + 14}">${esc(s)}</text>` : "") + "</g>";
  }).join("");
}

// ------------------------------------------------------------------ hubs
function renderHubs(d, cm) {
  const hubs = (d.funnel && d.funnel.hubs) || {};
  const pol = (d.policy && d.policy.contexts) || {};
  const ds = (d.cognee && d.cognee.datasets) || {};
  const sk = (d.skills && d.skills.owners) || {};
  $("hubs").innerHTML = CTX.map((c) => {
    const h = hubs[c] || {}, ct = cm["gateway-" + c];
    const mem = Object.entries(ds).filter(([, x]) => x.context === c).reduce((a, [, x]) => a + (x.items || 0), 0);
    return `<div class="card hub" style="--c:${COLOR[c]}">
      <div class="title"><h3>hub-${esc(c)}</h3>${h.up ? tag("ok", "● online") : tag("bad", "● down")}</div>
      <div class="url">${esc(h.url || "")}/mcp</div>
      <div class="tags">
        ${tag(ctState(ct) === "live" ? "ok" : "bad", "container " + (ct ? (ct.health || ct.state) : "missing"))}
        ${h.oauth_required ? tag("ok", "OAuth required") : tag("wn", "OAuth ?")}
        ${h.latency_ms != null ? tag("", h.latency_ms + " ms") : ""}
        ${h.error ? tag("bad", h.error) : ""}
      </div>
      <div class="stats">
        <div class="stat"><div class="n">${(pol[c] || {}).count ?? "—"}</div><div class="t">injectable secrets</div></div>
        <div class="stat"><div class="n">${d.cognee && d.cognee.ok ? mem : "—"}</div><div class="t">own memory items</div></div>
        <div class="stat"><div class="n">${(sk[c] || []).length + (sk.shared || []).length}</div><div class="t">skills (own + shared)</div></div>
      </div></div>`;
  }).join("");
}

// ------------------------------------------------------------------ secretos
function renderSecrets(d) {
  const inf = d.infisical || {}, pol = (d.policy && d.policy.contexts) || {}, si = d.static_import || {};
  const live = inf.ok ? inf.contexts || {} : {};
  const rows = CTX.map((c) => {
    const L = live[c];
    const n = L && L.ok ? L.count : si[c];
    return { c, n, liveOk: !!(L && L.ok), p: (pol[c] || {}).count || 0, L };
  });
  const max = Math.max(1, ...rows.map((r) => Math.max(r.n || 0, r.p)));
  const anyLive = rows.some((r) => r.liveOk);
  $("sec-src").textContent = anyLive ? "Infisical live · " + ago(inf.checked_at) : "static import from " + (si.date || "");
  let html = rows.map((r) => {
    const doms = Object.entries((pol[r.c] || {}).domains || {});
    const folders = r.L && r.L.folders ? Object.entries(r.L.folders).filter(([f]) => f !== "/") : [];
    return `<div class="row" style="--c:${COLOR[r.c]}"><div><b>${esc(r.c)}</b></div>
      <div class="bar"><i style="width:${(100 * (r.n || 0)) / max}%"></i><i class="policy" style="width:${(100 * r.p) / max}%"></i></div>
      <div class="num">${r.n ?? "—"} <span class="muted small">${r.liveOk ? "Infisical" : "import"}</span> · ${r.p} <span class="muted small">policy</span></div></div>
      <div class="chips">${doms.map(([k, v]) => `<span class="chip">${esc(k)} ×${v}</span>`).join("")}${folders.map(([f, v]) => `<span class="chip">📁 ${esc(f)} ${v}</span>`).join("")}${r.L && !r.L.ok ? `<span class="chip bad">${esc(r.L.error)}</span>` : ""}</div>`;
  }).join("");
  if (inf.shared && inf.shared.ok) {
    html += `<div class="row" style="--c:${COLOR.shared}"><div><b>shared</b></div><div class="bar"><i style="width:${(100 * inf.shared.count) / max}%"></i></div><div class="num">${inf.shared.count} <span class="muted small">Infisical</span></div></div>`;
  }
  html += `<div class="note">Colour bar: secrets stored in Infisical (counted with each gateway's <i>Viewer</i> identity, values never read). White line: what the gateway may inject per <span class="mono">config/secret-policy.yaml</span>. Chips: allowed domains. Import from ${esc(si.date)}: ${CTX.map((c) => esc(si[c] ?? "—")).join("/")}.</div>`;
  $("secrets").innerHTML = html;
}

// ------------------------------------------------------------------ memoria
function renderMemory(d) {
  const cg = d.cognee || {};
  $("cog-ver").textContent = cg.ok ? `v${cg.version} · ${ago(cg.checked_at)}` : "";
  if (cg.pending) { $("memory").innerHTML = '<p class="muted">loading…</p>'; return; }
  if (!cg.ok) { $("memory").innerHTML = `<p class="bad">Cognee not responding (${esc(cg.error)})</p>`; return; }
  const ds = Object.entries(cg.datasets || {});
  const max = Math.max(1, ...ds.map(([, x]) => x.items || 0));
  $("memory").innerHTML = ds.map(([n, x]) => `<div class="row" style="--c:${COLOR[x.context] || "var(--acc)"}">
      <div><b>${esc(n)}</b><div class="muted small">${esc(x.context)}</div></div>
      <div class="bar"><i style="width:${(100 * (x.items || 0)) / max}%"></i></div>
      <div class="num">${x.error ? `<span class="bad">${esc(x.error)}</span>` : `${x.items ?? 0} items${x.pending ? ` · <span class="wn">${x.pending} pending</span>` : ""}`}${x.nodes != null ? `<div class="muted small">${x.nodes} nodes · ${x.edges} edges</div>` : ""}</div></div>`).join("") +
    `<div class="note">Nodes/edges: the real graph of each dataset. "Pending": saved memories not yet in the graph (reprocessed by <span class="mono">scripts/cognify_pending.py</span>). Each gateway queries Cognee with its own API key and sees only its own datasets + <i>shared</i>. Only counts are shown, never content.</div>`;
}

// ------------------------------------------------------------------ contenedores
function renderContainers(d) {
  const c = d.containers || {};
  const items = c.items || [];
  $("ct-sum").textContent = c.pending ? "loading…" : c.ok ? `${items.filter((x) => ctState(x) === "live").length}/${items.length} healthy · ${ago(c.checked_at)}` : "docker not responding";
  $("containers").innerHTML = items.map((x) => `<div class="ct"><div><b>${esc(x.service)}</b><div class="img">${esc(x.image)}</div></div>
    <div style="display:flex;align-items:center;gap:8px"><span class="muted small">${esc(x.health || x.state)} · ${esc(x.status.replace(/^Up /, ""))}</span><span class="sdot ${ctState(x)}"></span></div></div>`).join("");
}

// ------------------------------------------------------------------ LLM / skills
function renderLlm(d) {
  const l = d.llm || {}, cfg = l.config || {};
  $("llm").innerHTML = `<div class="kv">
    <div>Ollama</div><div>${l.ollama_up ? `<span class="ok">● online</span> v${esc(l.ollama_version)}` : `<span class="bad">● not responding</span>`}</div>
    <div>Cognee LLM</div><div class="mono">${esc(cfg.LLM_PROVIDER)} / ${esc(cfg.LLM_MODEL)}</div>
    <div>Embeddings</div><div class="mono">${esc(cfg.EMBEDDING_PROVIDER)} / ${esc((cfg.EMBEDDING_MODEL || "").split("/").pop())}</div>
    <div>Installed</div><div>${(l.installed || []).map((m) => `<span class="chip">${esc(m)}</span>`).join(" ") || "—"}</div>
    <div>In memory</div><div>${(l.loaded || []).length ? (l.loaded || []).map((m) => `<span class="chip">${esc(m)}</span>`).join(" ") : '<span class="muted">none (loaded on use)</span>'}</div></div>`;
}
function renderSkills(d) {
  const s = d.skills || {};
  $("sk-head").textContent = s.ok ? `@${s.head} · ${ago(s.updated)}` : "";
  if (s.pending) { $("skills").innerHTML = '<p class="muted">loading…</p>'; return; }
  if (!s.ok) { $("skills").innerHTML = `<p class="bad">skills-sync not responding (${esc(s.error)})</p>`; return; }
  $("skills").innerHTML = `<div class="kv">${Object.entries(s.owners || {}).map(([o, l]) => `<div><span class="sdot" style="display:inline-block;background:${COLOR[o]}"></span> ${esc(o)}</div><div>${l.length ? l.map((n) => `<span class="chip">${esc(n)}</span>`).join(" ") : '<span class="muted">0</span>'}</div>`).join("")}</div>`;
}


// ------------------------------------------------------------------ watchdog
function renderWatchdog(d) {
  const w = d.watchdog || {};
  $("wd-head").textContent = w.schedule || "";
  if (w.pending) { $("watchdog").innerHTML = '<p class="muted">loading…</p>'; return; }
  if (!w.ok && w.error && !w.has_status) {
    $("watchdog").innerHTML = `<p class="bad">watchdog: ${esc(w.error)}</p>`;
    return;
  }
  if (!w.has_status) {
    $("watchdog").innerHTML = `<div class="kv">
      <div>Last run</div><div class="muted">not run yet</div>
      <div>LaunchAgent</div><div>${w.agent_loaded ? tag("ok", "loaded") : tag("bad", "not loaded")}</div></div>
      <div class="note">Install with <span class="mono">scripts/install_watchdog_agent.sh</span>.</div>`;
    return;
  }
  const hubs = w.hubs || {};
  const rows = CTX.map((c) => {
    const h = hubs[c] || {};
    const dns = h.dns_ok ? tag("ok", "DNS") : tag("bad", "DNS");
    const mcp = h.mcp_ok ? tag("ok", "MCP") : tag("bad", "MCP");
    const rec = h.last_recreate
      ? `${esc(fmtDate(h.last_recreate))} <span class="muted">(${ago(h.last_recreate)})</span>${h.last_recreate_reason ? ` · ${esc(h.last_recreate_reason)}` : ""}`
      : '<span class="muted">never</span>';
    const flag = h.recreated ? tag("wn", "recreated") : (h.cooldown ? tag("wn", "cooldown") : "");
    return `<div>${esc(c)}</div><div>${dns} ${mcp} ${flag}</div><div class="muted small">last recreate</div><div class="muted small">${rec}</div>`;
  }).join("");
  $("watchdog").innerHTML = `<div class="kv">
    <div>Last run</div><div>${w.last_run ? `${esc(fmtDate(w.last_run))} <span class="muted">(${ago(w.last_run)})</span> ${w.run_ok ? tag("ok", "OK") : tag("bad", "FAIL")}` : "—"}</div>
    <div>Cognee :8010</div><div>${w.cognee_ok ? tag("ok", "OK") : tag("bad", "FAIL")}</div>
    <div>Dashboard :8790</div><div>${w.dashboard_ok ? tag("ok", "OK") : tag("bad", "FAIL")}</div>
    <div>LaunchAgent</div><div>${w.agent_loaded ? `${tag("ok", "loaded")} <span class="muted">exit ${esc(w.agent_last_exit)}</span>` : tag("bad", "not loaded")}</div>
    ${rows}</div>
    <div class="note">Public DoH (1.1.1.1 / 8.8.8.8) + /mcp→401; recreates only ts-&lt;ctx&gt;+gateway-&lt;ctx&gt;; 30 min cooldown. Never enables Funnel.</div>`;
}
// ------------------------------------------------------------------ backup / git
function renderBackup(d) {
  const b = d.backup || {}, r = b.restic || {}, o = b.offsite || {};
  const off = o.last_run ? `${esc(fmtDate(o.last_run))} <span class="muted">(${ago(o.last_run)})</span> ${o.success ? `${tag("ok", "OK")} <span class="muted">${esc(o.remote)} · ${esc(o.size)}</span>` : `${tag("bad", "failed")} <span class="muted">${esc(o.error || "running or unfinished")}</span>`}` : '<span class="muted">never (scripts/offsite_sync.sh)</span>';
  $("backup").innerHTML = `<div class="kv">
    <div>Last backup</div><div>${b.last_run ? `${esc(fmtDate(b.last_run))} <span class="muted">(${ago(b.last_run)})</span> ${b.success ? tag("ok", "OK") : tag("bad", "failed")}` : "—"}</div>
    <div>Snapshot</div><div class="mono">${esc(b.snapshot || r.latest || "—")}</div>
    <div>Contents</div><div>${b.files != null ? `${b.files} files · ${esc(b.size)} in ${esc(b.duration)}` : "—"}</div>
    <div>New in repo</div><div>${b.added ? `${esc(b.added)} <span class="muted">(${esc(b.stored)} after compression)</span>` : "—"}</div>
    <div>Offsite copy (Drive)</div><div>${off}</div>
    <div>Total snapshots</div><div>${r.ok ? r.snapshots : `<span class="muted">${esc(r.error || "—")}</span>`}</div>
    <div>Next run</div><div>${esc(fmtDate(b.next_run))} <span class="muted">(${ago(b.next_run)})</span></div>
    <div>LaunchAgent</div><div>${b.agent_loaded ? `${tag("ok", "loaded")} <span class="muted">last exit ${esc(b.agent_last_exit)}</span>` : tag("bad", "not loaded")}</div></div>
    <div class="note">restic encrypts .env, cognee.env and the critical volumes; scheduled ${esc(b.schedule || "")} (host local time).</div>`;
}
function renderGit(d) {
  const g = d.git || {};
  $("branch").textContent = g.branch || "";
  $("commits").innerHTML = `<table>${(g.commits || []).map((c) => `<tr><td class="h">${esc(c.hash)}</td><td>${esc(c.subject)}</td><td class="d">${esc(ago(c.date))}</td></tr>`).join("")}</table>`;
}

// ------------------------------------------------------------------ ciclo
function overall(d, cm) {
  if (Object.values(d).some((v) => v && v.pending)) { $("overall").textContent = "Collecting data…"; $("pulse").className = "pulse warn"; return; }
  const hubs = (d.funnel && d.funnel.hubs) || {};
  const bad = [];
  if (!CTX.every((c) => hubs[c] && hubs[c].up)) bad.push("hubs");
  if (((d.containers && d.containers.items) || []).some((c) => ctState(c) !== "live")) bad.push("containers");
  if (!(d.cognee && d.cognee.ok)) bad.push("memory");
  if (!(d.backup && d.backup.success)) bad.push("backup");
  if (d.watchdog && d.watchdog.has_status && d.watchdog.run_ok === false) bad.push("watchdog");
  const p = $("pulse");
  p.className = "pulse " + (bad.length ? (bad.includes("hubs") ? "down" : "warn") : "live");
  $("overall").textContent = bad.length ? "Attention: " + bad.join(", ") : "All systems operational";
}
function render(d) {
  const cm = containerMap(d);
  const parts = [renderKpis, renderArch, renderHubs, renderSecrets, renderMemory, renderContainers, renderLlm, renderSkills, renderWatchdog, renderBackup, renderGit, overall];
  for (const f of parts) { try { f(d, cm); } catch (e) { console.error(f.name, e); } }
}
async function tick() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    last = await r.json(); lastAt = Date.now();
    render(last);
    if (Object.values(last).some((v) => v && v.pending)) setTimeout(tick, 2500); // primer arranque: completar rápido
  } catch (e) {
    $("overall").textContent = "No connection to the dashboard";
    $("pulse").className = "pulse down";
  }
}
setInterval(() => { if (lastAt) $("updated").textContent = `updated ${Math.round((Date.now() - lastAt) / 1000)} s ago · every ${REFRESH_MS / 1000} s`; }, 1000);
tick();
setInterval(tick, REFRESH_MS);
