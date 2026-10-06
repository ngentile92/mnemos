"use strict";
// Pestaña Explorar: grafo de memoria, memorias, skills y nombres de secretos del contexto elegido.
// Todo viene de /api/explore (el servidor usa la credencial de ESE contexto; "todos" = hub-admin).
(() => {
  const DS_COLOR = { work: "#38bdf8", shared: "#fbbf24", personal: "#a78bfa", side_shop: "#f472b6", side_blog: "#fb923c", side_mnemos: "#2dd4bf" };
  const CTX_COLOR = { work: "#38bdf8", personal: "#a78bfa", side: "#f472b6", shared: "#fbbf24" };
  const VIEWS = ["work", "personal", "side", "todos"];
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (iso) => iso ? new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").toLocaleString("es-ES", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  let view = localStorage.getItem("mnemos.view") || "work";
  let data = null, cy = null, inflight = null;

  // ---------------------------------------------------------------- pestañas
  function route() {
    const [tab, v] = location.hash.replace("#", "").split("/");
    const ex = tab === "explorar";
    $("view-estado").classList.toggle("hidden", ex);
    $("view-explorar").classList.toggle("hidden", !ex);
    $("tab-estado").classList.toggle("active", !ex);
    $("tab-explorar").classList.toggle("active", ex);
    if (ex) {
      if (v && VIEWS.includes(v) && v !== view) { view = v; data = null; }
      if (!data) load(); else if (cy) { cy.resize(); cy.fit(undefined, 30); }
    }
  }
  window.addEventListener("hashchange", route);

  // ---------------------------------------------------------------- carga
  // La última vista pedida siempre gana: si había una carga en curso se cancela (antes el click se
  // descartaba y el botón quedaba "muerto" porque `view` ya apuntaba a la vista nunca cargada).
  async function load(force) {
    if (inflight) inflight.abort();
    const req = inflight = new AbortController();
    localStorage.setItem("mnemos.view", view);
    for (const b of document.querySelectorAll("#ctx-seg button")) b.classList.toggle("on", b.dataset.view === view);
    $("ex-updated").textContent = "cargando…";
    $("ex-errors").innerHTML = "";
    try {
      const r = await fetch(`/api/explore?view=${encodeURIComponent(view)}${force ? "&fresh=1" : ""}`,
                            { cache: "no-store", signal: req.signal });
      const j = await r.json();
      if (req !== inflight) return;  // llegó tarde: ya se pidió otra vista
      if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
      data = j;
      render();
    } catch (e) {
      if (req !== inflight || e.name === "AbortError") return;
      $("ex-errors").innerHTML = `<div class="alert">No pude cargar la vista: ${esc(e.message)}</div>`;
      $("ex-updated").textContent = "";
    } finally { if (req === inflight) inflight = null; }
  }

  function render() {
    const d = data;
    $("ex-identity").textContent = `credencial: ${d.identity}`;
    $("ex-updated").textContent = "actualizado " + new Date().toLocaleTimeString("es-ES");
    $("ex-errors").innerHTML = (d.errors || []).map((e) => `<div class="alert">${esc(e)}</div>`).join("");
    const nodes = d.graph.nodes, common = nodes.filter((n) => n.common);
    const k = [
      ["Memorias", d.memories.length, `${d.memories.filter((m) => m.in_graph === false).length} pendientes de procesar`],
      ["Conceptos", nodes.filter((n) => n.type === "Entity").length, `${nodes.length} nodos en total`],
      ["Relaciones", d.graph.edges.length, `${d.datasets.length} datasets`],
      ["En común", common.length, view === "todos" ? "entre contextos" : "entre sus datasets"],
      ["Skills", d.skills.length, "shared + propias"],
      ["Secretos", d.secrets.length, "solo nombres"],
    ];
    $("ex-kpis").innerHTML = k.map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="d muted">${esc(s)}</div></div>`).join("");
    renderGraph();
    renderMemories();
    renderSkills();
    renderSecrets();
  }

  // ---------------------------------------------------------------- grafo
  function renderGraph() {
    const d = data;
    const onlyEnt = $("ex-entities").checked, onlyCommon = $("ex-common").checked;
    const keep = new Set();
    const els = [];
    const deg = {};
    for (const e of d.graph.edges) { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; }
    for (const n of d.graph.nodes) {
      if (onlyEnt && n.type !== "Entity") continue;
      if (onlyCommon && !n.common) continue;
      keep.add(n.id);
      const main = n.datasets.find((x) => x !== "shared") || n.datasets[0];
      els.push({ data: { id: n.id, label: n.type === "Entity" ? n.label : n.type, color: DS_COLOR[main] || "#94a3b8", size: Math.min(60, 16 + 4 * Math.sqrt(deg[n.id] || 0) * 2), kind: n.type === "Entity" ? "ent" : "doc" }, classes: (n.common ? "common " : "") + (n.type === "Entity" ? "ent" : "doc") });
    }
    for (const e of d.graph.edges) {
      if (keep.has(e.source) && keep.has(e.target)) els.push({ data: { id: `${e.source}→${e.target}→${e.label}`, source: e.source, target: e.target, label: e.label.replace(/_/g, " ") } });
    }
    if (cy) cy.destroy();
    if (!window.cytoscape) { $("cy").innerHTML = '<p class="muted">No cargó la librería del grafo.</p>'; return; }
    cy = window.cytoscape({
      container: $("cy"), elements: els, wheelSensitivity: 0.25, minZoom: 0.15, maxZoom: 3,
      style: [
        { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(label)", color: "#e7ecf7", "font-size": 10, "text-valign": "bottom", "text-margin-y": 4, "text-outline-color": "#070a13", "text-outline-width": 2, "text-max-width": 120, "text-wrap": "ellipsis", "border-width": 1, "border-color": "rgba(255,255,255,.35)" } },
        { selector: "node.doc", style: { shape: "round-rectangle", opacity: 0.55, "font-size": 8, color: "#8d97b3" } },
        { selector: "node.common", style: { "border-width": 4, "border-color": "#fde047", "underlay-color": "#fde047", "underlay-opacity": 0.18, "underlay-padding": 8, "underlay-shape": "ellipse", "font-weight": "bold", "font-size": 12, "z-index": 10 } },
        { selector: "edge", style: { width: 1.2, "line-color": "rgba(148,163,209,.28)", "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "rgba(148,163,209,.35)", "arrow-scale": 0.6 } },
        { selector: "edge.show, edge:selected", style: { label: "data(label)", "font-size": 8, color: "#c3cbe3", "text-rotation": "autorotate", "text-background-color": "#070a13", "text-background-opacity": 0.8, "line-color": "#a5b4fc", "target-arrow-color": "#a5b4fc", width: 2 } },
        { selector: "node:selected", style: { "border-width": 4, "border-color": "#fff" } },
        { selector: ".faded", style: { opacity: 0.12 } },
        { selector: ".hit", style: { "border-width": 5, "border-color": "#22d3ee" } },
      ],
      layout: { name: "cose", animate: false, randomize: true, nodeRepulsion: 9000, idealEdgeLength: 70, gravity: 0.35, numIter: 1500, padding: 30 },
    });
    cy.on("tap", "node", (ev) => select(ev.target));
    cy.on("tap", (ev) => { if (ev.target === cy) { cy.elements().removeClass("faded show"); } });
    const present = [...new Set(d.graph.nodes.flatMap((n) => n.datasets))];
    $("ex-legend").className = "legend dsleg";
    $("ex-legend").innerHTML = present.map((ds) => `<span class="lgi"><i style="background:${DS_COLOR[ds] || "#94a3b8"}"></i>${esc(ds)}</span>`).join("") +
      `<span class="lgi"><i class="ring"></i>en común (${view === "todos" ? "entre contextos" : "entre datasets"})</span><span class="lgi muted">tamaño = cantidad de conexiones</span>`;
    if (!els.length) $("ex-node").innerHTML = `<p class="muted">${d.graph.nodes.length ? "Nada con estos filtros." : "Todavía no hay grafo para este contexto (las memorias pendientes se procesan en segundo plano)."}</p>`;
  }

  function select(node) {
    const n = data.graph.nodes.find((x) => x.id === node.id());
    if (!n) return;
    cy.elements().addClass("faded").removeClass("show");
    const hood = node.closedNeighborhood();
    hood.removeClass("faded"); hood.edges().addClass("show");
    const neigh = node.connectedEdges().map((e) => {
      const other = e.source().id() === node.id() ? e.target() : e.source();
      const out = e.source().id() === node.id();
      return `<li>${out ? "→" : "←"} <i>${esc(e.data("label"))}</i> <b>${esc(other.data("label"))}</b></li>`;
    }).slice(0, 25).join("");
    $("ex-node").innerHTML = `<h4>${esc(n.label)}</h4><div class="tags">${n.datasets.map((ds) => `<span class="tag" style="border-color:${DS_COLOR[ds] || "#94a3b8"}">${esc(ds)}</span>`).join("")}${n.common ? '<span class="tag wn">en común</span>' : ""}</div>
      <p class="muted small">${esc(n.type)}${n.contexts.length > 1 ? " · contextos: " + esc(n.contexts.join(", ")) : ""}</p>
      ${n.desc ? `<p>${esc(n.desc)}</p>` : ""}${neigh ? `<p class="muted small">Conexiones</p><ul class="nlist">${neigh}</ul>` : ""}`;
  }

  function search(q) {
    if (!cy) return;
    cy.nodes().removeClass("hit");
    q = q.trim().toLowerCase();
    if (!q) return;
    const hits = cy.nodes().filter((n) => String(n.data("label")).toLowerCase().includes(q));
    hits.addClass("hit");
    if (hits.length) { cy.animate({ fit: { eles: hits, padding: 80 } }, { duration: 300 }); if (hits.length === 1) select(hits[0]); }
  }

  // ---------------------------------------------------------------- memorias / skills / secretos
  function renderMemories() {
    const ms = data.memories;
    $("ex-mem-sum").textContent = `${ms.length} en ${data.datasets.length} datasets`;
    $("ex-memories").innerHTML = ms.length ? ms.map((m, i) => `<div class="mem" style="--c:${DS_COLOR[m.dataset] || "#94a3b8"}" data-i="${i}">
      <div class="mh"><span class="tag">${esc(m.dataset)}</span><span class="muted small">${esc(fmt(m.created_at))}</span>
        ${m.source_app ? `<span class="tag">app: ${esc(m.source_app)}</span>` : '<span class="tag muted">app: sin registro</span>'}
        ${m.in_graph === true ? '<span class="tag ok">en el grafo</span>' : m.in_graph === false ? '<span class="tag wn">pendiente de procesar</span>' : ""}
        ${(m.tags || []).map((t) => `<span class="chip">#${esc(t)}</span>`).join("")}</div>
      <div class="mt">${m.text != null ? esc(m.text) + (m.truncated ? "…" : "") : `<span class="muted">(sin texto: ${esc(m.text_error || "no es texto")})</span>`}</div>
      ${m.text && m.text.length > 380 ? '<button class="more">ver completo</button>' : ""}</div>`).join("") : '<p class="muted">Sin memorias en este contexto.</p>';
  }
  $("ex-memories").addEventListener("click", (ev) => {
    const b = ev.target.closest(".more"); if (!b) return;
    const card = b.closest(".mem"); card.classList.toggle("open");
    b.textContent = card.classList.contains("open") ? "ver menos" : "ver completo";
  });

  function renderSkills() {
    const sk = data.skills;
    $("ex-skill").classList.add("hidden");
    $("ex-skills").innerHTML = sk.length ? sk.map((s) => `<div class="sk" data-name="${esc(s.name)}"><div><b>${esc(s.name)}</b><p>${esc(s.description)}</p></div><span class="tag" style="border-color:${CTX_COLOR[s.owner] || "#94a3b8"}">${esc(s.owner)}</span></div>`).join("") : '<p class="muted">No hay skills visibles.</p>';
  }
  $("ex-skills").addEventListener("click", async (ev) => {
    const el = ev.target.closest(".sk"); if (!el) return;
    for (const x of document.querySelectorAll(".sk")) x.classList.toggle("on", x === el);
    const box = $("ex-skill");
    box.classList.remove("hidden"); box.innerHTML = '<p class="muted">cargando…</p>';
    try {
      const r = await fetch(`/api/skill?view=${encodeURIComponent(view)}&name=${encodeURIComponent(el.dataset.name)}`, { cache: "no-store" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.status);
      box.innerHTML = `<div class="card-h"><b>${esc(j.owner)}/${esc(j.name)}/SKILL.md</b><span class="muted small">${j.files.length} archivo(s)</span></div><pre>${esc(j.content)}</pre>`;
    } catch (e) { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
  });

  function renderSecrets() {
    const s = data.secrets;
    $("ex-sec-sum").textContent = `${s.length} · policy de inyección`;
    $("ex-secrets").innerHTML = s.length ? s.map((x) => `<div class="sec"><div><b>${esc(x.name)}</b>${view === "todos" ? `<div class="muted small">${esc(x.context)}</div>` : ""}</div>
      <div>${x.hosts.map((h) => `<span class="chip">${esc(h)}</span>`).join(" ")}${x.description ? `<div class="muted small">${esc(x.description)}</div>` : ""}</div></div>`).join("") : '<p class="muted">Sin secretos en la policy.</p>';
  }

  // ---------------------------------------------------------------- controles
  $("ctx-seg").addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b || b.dataset.view === view) return;
    view = b.dataset.view; data = null;
    history.replaceState(null, "", `#explorar/${view}`);
    load();
  });
  $("ex-refresh").addEventListener("click", () => { data = null; load(true); });
  $("ex-entities").addEventListener("change", () => data && renderGraph());
  $("ex-common").addEventListener("change", () => data && renderGraph());
  $("ex-fit").addEventListener("click", () => cy && cy.fit(undefined, 30));
  let t = null;
  $("ex-search").addEventListener("input", (ev) => { clearTimeout(t); t = setTimeout(() => search(ev.target.value), 250); });
  route();
})();
