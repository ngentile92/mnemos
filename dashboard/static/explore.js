"use strict";
// Pestaña Explorar: grafo de memoria, memorias, skills y nombres de secretos del contexto elegido.
// Todo viene de /api/explore (el servidor usa la credencial de ESE contexto; "todos" = hub-admin).
(() => {
  // contextos y datasets de config/contexts.yaml (/config.js); colores por posición
  const CFG = window.MNEMOS || { contexts: ["work", "personal", "side"].map((name) => ({ name, datasets: [name] })) };
  const PALETTE = ["#38bdf8", "#a78bfa", "#f472b6", "#34d399", "#fb923c", "#2dd4bf", "#e879f9"];
  const EXTRA = ["#fb923c", "#2dd4bf", "#e879f9", "#a3e635", "#f87171"];  // 2º, 3º... dataset de un contexto
  const CTX_COLOR = { shared: "#fbbf24" }, DS_COLOR = { shared: "#fbbf24" };
  CFG.contexts.forEach((c, i) => {
    CTX_COLOR[c.name] = PALETTE[i % PALETTE.length];
    (c.datasets || [c.name]).forEach((d, j) => { DS_COLOR[d] = j ? EXTRA[(j - 1) % EXTRA.length] : CTX_COLOR[c.name]; });
  });
  const VIEWS = [...CFG.contexts.map((c) => c.name), "todos"];
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (iso) => iso ? new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").toLocaleString("es-ES", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  let view = localStorage.getItem("mnemos.view");
  if (!VIEWS.includes(view)) view = VIEWS[0];
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
      $("ex-errors").innerHTML = `<div class="alert">Could not load the view: ${esc(e.message)}</div>`;
      $("ex-updated").textContent = "";
    } finally { if (req === inflight) inflight = null; }
  }

  function render() {
    const d = data;
    $("ex-identity").textContent = `credencial: ${d.identity}`;
    $("ex-updated").textContent = "updated " + new Date().toLocaleTimeString();
    $("ex-errors").innerHTML = (d.errors || []).map((e) => `<div class="alert">${esc(e)}</div>`).join("");
    const nodes = d.graph.nodes, common = nodes.filter((n) => n.common);
    const k = [
      ["Memories", d.memories.length, `${d.memories.filter((m) => m.in_graph === false).length} pending processing`],
      ["Concepts", nodes.filter((n) => n.type === "Entity").length, `${nodes.length} nodes in total`],
      ["Relations", d.graph.edges.length, `${d.datasets.length} datasets`],
      ["Shared", common.length, view === "todos" ? "across contexts" : "across its datasets"],
      ["Skills", d.skills.length, "shared + own"],
      ["Secrets", d.secrets.length, "names only"],
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
    if (!window.cytoscape) { $("cy").innerHTML = '<p class="muted">The graph library did not load.</p>'; return; }
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
      `<span class="lgi"><i class="ring"></i>shared (${view === "todos" ? "across contexts" : "across datasets"})</span><span class="lgi muted">size = number of connections</span>`;
    if (!els.length) $("ex-node").innerHTML = `<p class="muted">${d.graph.nodes.length ? "Nothing matches these filters." : "No graph for this context yet (pending memories are processed in the background)."}</p>`;
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
    $("ex-node").innerHTML = `<h4>${esc(n.label)}</h4><div class="tags">${n.datasets.map((ds) => `<span class="tag" style="border-color:${DS_COLOR[ds] || "#94a3b8"}">${esc(ds)}</span>`).join("")}${n.common ? '<span class="tag wn">shared</span>' : ""}</div>
      <p class="muted small">${esc(n.type)}${n.contexts.length > 1 ? " · contexts: " + esc(n.contexts.join(", ")) : ""}</p>
      ${n.desc ? `<p>${esc(n.desc)}</p>` : ""}${neigh ? `<p class="muted small">Connections</p><ul class="nlist">${neigh}</ul>` : ""}`;
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
        ${m.source_app ? `<span class="tag">app: ${esc(m.source_app)}</span>` : '<span class="tag muted">app: unknown</span>'}
        ${m.in_graph === true ? '<span class="tag ok">in graph</span>' : m.in_graph === false ? '<span class="tag wn">pending processing</span>' : ""}
        ${(m.tags || []).map((t) => `<span class="chip">#${esc(t)}</span>`).join("")}</div>
      <div class="mt">${m.text != null ? esc(m.text) + (m.truncated ? "…" : "") : `<span class="muted">(no text: ${esc(m.text_error || "not text")})</span>`}</div>
      ${m.text && m.text.length > 380 ? '<button class="more">show all</button>' : ""}</div>`).join("") : '<p class="muted">No memories in this context.</p>';
  }
  $("ex-memories").addEventListener("click", (ev) => {
    const b = ev.target.closest(".more"); if (!b) return;
    const card = b.closest(".mem"); card.classList.toggle("open");
    b.textContent = card.classList.contains("open") ? "show less" : "show all";
  });

  function renderSkills() {
    const sk = data.skills;
    $("ex-skill").classList.add("hidden");
    $("ex-skills").innerHTML = sk.length ? sk.map((s) => `<div class="sk" data-name="${esc(s.name)}"><div><b>${esc(s.name)}</b><p>${esc(s.description)}</p></div><span class="tag" style="border-color:${CTX_COLOR[s.owner] || "#94a3b8"}">${esc(s.owner)}</span></div>`).join("") : '<p class="muted">No visible skills.</p>';
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
      box.innerHTML = `<div class="card-h"><b>${esc(j.owner)}/${esc(j.name)}/SKILL.md</b><span class="muted small">${j.files.length} file(s)${CFG.skills_editable ? ' <button id="ex-skill-edit">Edit</button>' : ""}</span></div><pre>${esc(j.content)}</pre>`;
      if (CFG.skills_editable) $("ex-skill-edit").addEventListener("click", () => skillEditor(j.owner, j.content, j.name));
    } catch (e) { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
  });

  // ---------------------------------------------------------------- editor de skills (solo modo carpeta local)
  const OWNERS = ["shared", ...CFG.contexts.map((c) => c.name)];
  const TEMPLATE = (n) => `---\nname: ${n}\ndescription: One sentence: what it does and when to use it.\n---\n\n# ${n}\n\nSteps to follow.\n`;
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  window.mnemosPost = post;
  function skillEditor(owner, content, edit) {
    const box = $("ex-skill");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="card-h"><b>${edit ? "Edit " + esc(edit) : "New skill"}</b>
      <select id="sk-owner" ${edit ? "disabled" : ""}>${OWNERS.map((o) => `<option ${o === owner ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></div>
      <p class="muted small">Folder = <code>name</code>. <code>shared</code> is visible in every context. Validated with the same rules as the hubs.</p>
      <textarea id="sk-text" rows="16" style="width:100%;font-family:monospace">${esc(content)}</textarea>
      <div><button id="sk-save">Save</button> <button id="sk-cancel">Cancel</button> <span id="sk-msg" class="small"></span></div>`;
    $("sk-cancel").addEventListener("click", () => box.classList.add("hidden"));
    $("sk-save").addEventListener("click", async () => {
      $("sk-msg").className = "small muted"; $("sk-msg").textContent = "saving…";
      try {
        const j = await post("/api/skills/save", { owner: $("sk-owner").value, content: $("sk-text").value, edit: edit || null });
        $("sk-msg").className = "small ok"; $("sk-msg").textContent = `saved ${j.path} — the hubs pick it up within seconds`;
        data = null; setTimeout(() => load(true), 600);
      } catch (e) { $("sk-msg").className = "small bad"; $("sk-msg").textContent = e.message; }
    });
  }
  if (CFG.skills_editable) {
    $("ex-skill-new").classList.remove("hidden");
    $("ex-skill-new").addEventListener("click", () => skillEditor(view === "todos" ? "shared" : view, TEMPLATE("my-skill")));
  }

  function renderSecrets() {
    const s = data.secrets;
    $("ex-sec-sum").textContent = `${s.length} · injection policy`;
    $("ex-secrets").innerHTML = s.length ? s.map((x) => `<div class="sec"><div><b>${esc(x.name)}</b>${view === "todos" ? `<div class="muted small">${esc(x.context)}</div>` : ""}</div>
      <div>${x.hosts.map((h) => `<span class="chip">${esc(h)}</span>`).join(" ")}${x.description ? `<div class="muted small">${esc(x.description)}</div>` : ""}</div></div>`).join("") : '<p class="muted">No secrets in the policy.</p>';
  }

  // ---------------------------------------------------------------- controles
  // un botón por contexto, antes de "Todos"
  $("ctx-seg").insertAdjacentHTML("afterbegin", CFG.contexts.map((c) =>
    `<button data-view="${esc(c.name)}" class="ctx" style="--c:${CTX_COLOR[c.name]}">${esc(c.name.charAt(0).toUpperCase() + c.name.slice(1))}</button>`).join(""));
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
