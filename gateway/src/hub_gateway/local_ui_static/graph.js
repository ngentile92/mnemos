"use strict";
// Graph tab (0.12.3): a living map of your memory; while the local model reads notes, new things pop in live. Canvas + d3-force (bundled in /ui/vendor, no CDN).
// Spaces are galaxies (each with its halo); notes shared between spaces are bridges. Continuous physics with
// centre gravity, collision, idle breathing; unconnected things orbit their galaxy. Fly-in, "how it grew"
// time-lapse, path between two things, PNG / video export, presentation mode.
// Explore interactions ported from Francisco Rua's dashboard work (#132, #133/#141): drag pulls neighbours,
// hide shared, open the notes behind a node, subject-first relations, one name per concept, "how to read this".
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (p) => window.MNEMOS_API(p);
  const T = (x) => (window.tr || ((y) => y))(x);
  const F = (x, o) => Object.entries(o).reduce((a, [k, v]) => a.split("{" + k + "}").join(v), T(x));
  const d3 = window.d3;
  const TYPE_C = { person: "#ff6fb5", org: "#ffc145", project: "#8b6cff", tool: "#2fd8ff", place: "#3ef0a4", topic: "#b7f34a", other: "#a5b4d8" };
  const TYPE_L = { person: "people", org: "organisations", project: "projects", tool: "tools", place: "places", topic: "topics", other: "other" };
  const SPACE_C = ["#8b6cff", "#2fd8ff", "#ff6fb5", "#3ef0a4", "#ffc145", "#ff8a5b"];
  const PULL = 0.6;              // share of a drag applied to the dragged node's direct neighbours (Francisco Rua, #132)
  const LABELS = 15;             // always-on labels: the most important things
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  let G = null, sim = null, nodes = [], links = [], byId = new Map(), adj = new Map();
  let cam = { x: 0, y: 0, k: 1 }, target = null, hover = null, sel = null, path = null, t0 = performance.now();
  let reveal = null;             // time-lapse: {start, dur, order} → nodes appear by first-seen date
  let galaxies = [], running = false, cv = null, ctx = null, dpr = 1, W = 0, H = 0, stars = [];
  let drag = null, pres = false, rec = null;
  let sparks = [], msgI = 0, msgT = null, seenFound = new Set();   // live build: particles + rotating messages
  const ES = (navigator.language || "").toLowerCase().startsWith("es");
  const MSGS = ES
    ? ["Conectando {a} con {b}…", "Atando cabos: {a} → {b}", "¡Ajá! {a} {r} {b}", "Siguiendo el hilo de {a}…", "{a} y {b} se conocen", "Ubicando {b} en tu galaxia…"]
    : ["Connecting {a} with {b}…", "Joining the dots: {a} → {b}", "Aha! {a} {r} {b}", "Following the thread of {a}…", "{a} and {b} know each other", "Placing {b} in your galaxy…"];
  const IDLE = ES ? ["Leyendo tus notas…", "Buscando quién conoce a quién…", "Encendiendo estrellas…"]
    : ["Reading your notes…", "Finding who knows whom…", "Lighting up stars…"];

  const spaceInfo = (n) => ((window.MNEMOS_SPACES || {}).spaces || []).find((s) => s.name === n);
  const spaceColour = (name, i) => (spaceInfo(name) || {}).color || (name === "shared" ? "#e5e7eb" : SPACE_C[i % SPACE_C.length]);
  const spaceLabel = (name) => T((spaceInfo(name) || {}).label || (name === "shared" ? "Shared" : name[0].toUpperCase() + name.slice(1)));
  const colourBy = () => ($("g-colour") || {}).value || "type";

  function colourOf(n) {
    const by = colourBy();
    if (by === "space") return n.bridge ? "#ffffff" : (galaxies.find((g) => g.name === n.home) || {}).colour || TYPE_C.other;
    if (by === "cluster") return n.cluster >= 0 ? SPACE_C.concat(Object.values(TYPE_C))[n.cluster % 12] : TYPE_C.other;
    return TYPE_C[n.type] || TYPE_C.other;
  }

  // ------------------------------------------------------------------ data → simulation
  function build(g) {
    G = g;
    const hideShared = $("g-hideshared") && $("g-hideshared").checked;
    let ns = g.nodes.filter((n) => !(hideShared && (n.spaces || []).length && n.spaces.every((s) => s === "shared")));
    const keep = new Set(ns.map((n) => n.id));
    const old = new Map(nodes.map((n) => [n.id, n]));
    const oldL = new Set(links.map((l) => l.source.id + "\u0001" + l.target.id));
    const live = old.size > 0, now = performance.now();
    const spaces = [...new Set(ns.flatMap((n) => n.spaces || []))].filter((s) => s !== "shared").sort();
    const R = Math.max(260, 120 * Math.sqrt(ns.length));
    galaxies = spaces.map((name, i) => {
      const a = spaces.length === 1 ? 0 : Math.PI + (2 * Math.PI * i) / spaces.length;
      const d = spaces.length === 1 ? 0 : R * 1.05;
      return { name, label: spaceLabel(name), colour: spaceColour(name, i), x: Math.cos(a) * d, y: Math.sin(a) * d * 0.7, r: 120 };
    });
    const gal = new Map(galaxies.map((x) => [x.name, x]));
    const max = Math.max(1, ...ns.map((n) => n.count || 1));
    nodes = ns.map((n, i) => {
      const own = (n.spaces || []).filter((s) => s !== "shared");
      const bridge = own.length > 1 || ((n.spaces || []).includes("shared") && own.length !== 1);
      const home = own.length === 1 ? own[0] : null;
      const o = old.get(n.id), c = gal.get(home) || { x: 0, y: 0 };
      const bornAt = o ? o.bornAt : live ? now + Math.random() * 1500 : 0;   // new while building → pops in
      return { ...n, home, bridge, imp: (n.count || 1) / max, r: 5 + 17 * Math.sqrt((n.count || 1) / max),
               phase: i * 1.7, x: o ? o.x : c.x + (Math.random() - 0.5) * 20, y: o ? o.y : c.y + (Math.random() - 0.5) * 20,
               vx: o ? o.vx : 0, vy: o ? o.vy : 0, bornAt, sparked: o ? o.sparked : !live };
    });
    byId = new Map(nodes.map((n) => [n.id, n]));
    links = g.edges.filter((e) => keep.has(e.source) && keep.has(e.target) && !["is a", "is_a"].includes(e.label))
      .map((e) => ({ ...e, source: byId.get(e.source), target: byId.get(e.target),
                     bornAt: live && !oldL.has(e.source + "\u0001" + e.target) ? now + Math.random() * 1500 : 0 }));
    adj = new Map(nodes.map((n) => [n.id, new Set()]));
    for (const l of links) { adj.get(l.source.id).add(l.target.id); adj.get(l.target.id).add(l.source.id); }
    const ranked = [...nodes].filter((n) => n.kind !== "me").sort((a, b) => b.count - a.count);
    ranked.slice(0, LABELS).forEach((n) => (n.top = true));
    nodes.forEach((n) => { n.lonely = !adj.get(n.id).size; n.colour = colourOf(n); });
    for (const gx of galaxies) {
      const mine = nodes.filter((n) => n.home === gx.name);
      mine.filter((n) => n.lonely).forEach((n, i, arr) => { n.orbit = { a: (2 * Math.PI * i) / Math.max(1, arr.length), g: gx }; });
    }
    if (sim) sim.stop();
    const centreOf = (n) => {
      if (n.home && gal.get(n.home)) return gal.get(n.home);
      const own = (n.spaces || []).map((s) => gal.get(s)).filter(Boolean);
      if (!own.length) return { x: 0, y: 0 };
      return { x: own.reduce((s, x) => s + x.x, 0) / own.length, y: own.reduce((s, x) => s + x.y, 0) / own.length };
    };
    sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((n) => n.id).distance((l) => 46 + 10 * (l.source.r + l.target.r) / 10).strength((l) => (l.source.home && l.target.home && l.source.home !== l.target.home ? 0.03 : Math.min(0.9, 0.15 + 0.12 * (l.weight || 1)))))
      .force("charge", d3.forceManyBody().strength((n) => -110 - 260 * n.imp).distanceMax(520))
      .force("collide", d3.forceCollide((n) => n.r + 7 + (n.top ? 10 : 0)).strength(0.9).iterations(2))
      .force("gx", d3.forceX((n) => centreOf(n).x).strength((n) => (n.bridge ? 0.08 : 0.055)))
      .force("gy", d3.forceY((n) => centreOf(n).y).strength((n) => (n.bridge ? 0.08 : 0.055)))
      .force("orbit", orbitForce)
      .alphaDecay(0.02).velocityDecay(0.32)
      .alphaTarget(reduce ? 0 : 0.012)        // never fully still: the graph breathes
      .on("tick", () => {});
    sim.stop();                                // we tick it from our own frame loop
    if (!live) for (let i = 0; i < 160; i++) sim.tick(); // settle before the first frame
    else sim.alpha(0.35);                     // a live refresh: let the newcomers find their place in view
    for (const gx of galaxies) fitGalaxy(gx);
  }
  // unconnected things orbit just outside their galaxy instead of sitting in a grid
  function orbitForce(alpha) {
    const t = (performance.now() - t0) / 1000;
    for (const n of nodes) {
      if (!n.orbit || n === (drag && drag.n)) continue;
      const g = n.orbit.g, rr = g.r + 40 + n.r, a = n.orbit.a + t * 0.035;
      n.vx += (g.x + Math.cos(a) * rr - n.x) * 0.06 * Math.max(alpha, 0.2);
      n.vy += (g.y + Math.sin(a) * rr * 0.8 - n.y) * 0.06 * Math.max(alpha, 0.2);
    }
  }
  function fitGalaxy(gx) {
    const mine = nodes.filter((n) => n.home === gx.name && !n.orbit);
    if (!mine.length) { gx.cx = gx.x; gx.cy = gx.y; gx.r = 90; return; }
    const cx = mine.reduce((s, n) => s + n.x, 0) / mine.length, cy = mine.reduce((s, n) => s + n.y, 0) / mine.length;
    gx.cx = cx; gx.cy = cy;
    gx.r = Math.max(90, Math.max(...mine.map((n) => Math.hypot(n.x - cx, n.y - cy) + n.r)) + 30);
  }

  // ------------------------------------------------------------------ camera
  function bounds() {
    const vis = nodes.filter(visible);
    if (!vis.length) return { x: 0, y: 0, k: 1 };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of vis) { x0 = Math.min(x0, n.x - n.r - 60); x1 = Math.max(x1, n.x + n.r + 60); y0 = Math.min(y0, n.y - n.r - 40); y1 = Math.max(y1, n.y + n.r + 40); }
    const k = Math.min(2.2, Math.min(W / (x1 - x0), H / (y1 - y0)));
    return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, k };
  }
  function fit(fly) {
    const b = bounds();
    if (fly && !reduce) { cam = { x: b.x, y: b.y, k: b.k * 0.18 }; }
    target = b;
  }
  const toWorld = (px, py) => ({ x: (px - W / 2) / cam.k + cam.x, y: (py - H / 2) / cam.k + cam.y });

  // ------------------------------------------------------------------ drawing
  function visible(n) {
    if (!reveal) return true;
    return n._shown !== undefined && n._shown <= (performance.now() - reveal.start) / reveal.dur;
  }
  function resize() {
    const box = cv.parentElement.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(300, box.width); H = Math.max(360, box.height);
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
    stars = Array.from({ length: 160 }, () => ({ x: Math.random(), y: Math.random(), r: Math.random() * 1.2 + 0.2, a: Math.random() * 0.5 + 0.1 }));
  }
  function frame() {
    if (!running) return;
    if (document.hidden) { running = false; return; }   // window hidden: stop; visibilitychange restarts it
    requestAnimationFrame(frame);
    const t = (performance.now() - t0) / 1000;
    if (sim && !reduce) sim.tick();
    if (target) {                                  // smooth camera (fly-in / fit / focus)
      const e = 0.075;
      cam.x += (target.x - cam.x) * e; cam.y += (target.y - cam.y) * e; cam.k *= Math.pow(target.k / cam.k, e);
      if (Math.abs(target.k / cam.k - 1) < 0.002 && Math.hypot(target.x - cam.x, target.y - cam.y) < 0.5) target = null;
    }
    if (pres && !target && !drag) { cam.x += Math.sin(t * 0.07) * 0.08; cam.y += Math.cos(t * 0.05) * 0.06; }
    for (const gx of galaxies) fitGalaxy(gx);
    draw(t);
  }
  function draw(t) {
    const c = ctx;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = c.createRadialGradient(W * 0.5, H * 0.45, 0, W * 0.5, H * 0.5, Math.max(W, H) * 0.75);
    bg.addColorStop(0, pres ? "#141032" : "#10132a"); bg.addColorStop(1, "#05060f");
    c.fillStyle = bg; c.fillRect(0, 0, W, H);
    for (const s of stars) {                       // faint parallax starfield
      const tw = s.a * (0.7 + 0.3 * Math.sin(t * 1.3 + s.x * 40));
      c.fillStyle = `rgba(200,210,255,${tw})`;
      c.beginPath(); c.arc(((s.x * W - cam.x * 0.04 * s.r) % W + W) % W, ((s.y * H - cam.y * 0.04 * s.r) % H + H) % H, s.r, 0, 7); c.fill();
    }
    c.save();
    c.translate(W / 2, H / 2); c.scale(cam.k, cam.k); c.translate(-cam.x, -cam.y);
    // galaxies: soft halo + name
    for (const gx of galaxies) {
      const r = gx.r * (1 + 0.015 * Math.sin(t * 0.6 + gx.x));
      const h = c.createRadialGradient(gx.cx, gx.cy, r * 0.1, gx.cx, gx.cy, r * 1.25);
      h.addColorStop(0, hexA(gx.colour, 0.26)); h.addColorStop(0.55, hexA(gx.colour, 0.1)); h.addColorStop(1, hexA(gx.colour, 0));
      c.fillStyle = h; c.beginPath(); c.arc(gx.cx, gx.cy, r * 1.25, 0, 7); c.fill();
      c.strokeStyle = hexA(gx.colour, 0.32); c.setLineDash([3 / cam.k, 9 / cam.k]); c.lineWidth = 1 / cam.k;
      c.beginPath(); c.arc(gx.cx, gx.cy, r, 0, 7); c.stroke(); c.setLineDash([]);
      c.font = `700 ${Math.max(14, 18 / cam.k)}px Inter, system-ui, sans-serif`; c.textAlign = "center";
      c.fillStyle = hexA(gx.colour, 0.95); c.fillText(gx.label.toUpperCase(), gx.cx, gx.cy - r - 12 / cam.k);
    }
    const focus = sel || hover, near = focus ? adj.get(focus.id) : null;
    const onPath = path ? new Set(path) : null;
    // edges: gradient from one end's colour to the other's; the path or the focus glows
    for (const l of links) {
      const a = l.source, b = l.target;
      if (!visible(a) || !visible(b)) continue;
      const pe = onPath && onPath.has(a.id) && onPath.has(b.id) && Math.abs(path.indexOf(a.id) - path.indexOf(b.id)) === 1;
      const fe = focus && (a === focus || b === focus);
      const dim = (onPath && !pe) || (focus && !fe && !onPath);
      const gr = c.createLinearGradient(a.x, a.y, b.x, b.y);
      const al = pe ? 0.95 : fe ? 0.8 : dim ? 0.05 : 0.28;
      gr.addColorStop(0, hexA(a.colour, al)); gr.addColorStop(1, hexA(b.colour, al));
      c.strokeStyle = gr; c.lineWidth = (pe ? 3 : fe ? 2 : 0.6 + 0.5 * Math.min(4, l.weight || 1)) / Math.sqrt(cam.k);
      const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.08, my = (a.y + b.y) / 2 - (b.x - a.x) * 0.08;
      const age = l.bornAt ? (performance.now() - l.bornAt) / 900 : 9;
      if (age < 0) continue;
      if (age < 1) {                               // a new relation draws itself, with a comet at its tip
        const u = 1 - Math.pow(1 - age, 3), q = 1 - u;
        c.strokeStyle = hexA(b.colour, 0.95); c.lineWidth = 2.4 / Math.sqrt(cam.k);
        c.beginPath(); c.moveTo(a.x, a.y);
        for (let i = 1; i <= 16; i++) { const v = (u * i) / 16, w = 1 - v; c.lineTo(w * w * a.x + 2 * w * v * mx + v * v * b.x, w * w * a.y + 2 * w * v * my + v * v * b.y); }
        c.stroke();
        const sx = q * q * a.x + 2 * q * u * mx + u * u * b.x, sy = q * q * a.y + 2 * q * u * my + u * u * b.y;
        c.fillStyle = "#fff"; c.shadowColor = b.colour; c.shadowBlur = 22;
        c.beginPath(); c.arc(sx, sy, 3.4 / Math.sqrt(cam.k), 0, 7); c.fill(); c.shadowBlur = 0;
        continue;
      }
      if (age < 2.5) { c.strokeStyle = hexA(b.colour, 0.28 + 0.5 * (2.5 - age) / 1.5); c.lineWidth = (1 + (2.5 - age)) / Math.sqrt(cam.k); }
      c.beginPath(); c.moveTo(a.x, a.y); c.quadraticCurveTo(mx, my, b.x, b.y); c.stroke();
      if ((pe || fe) && !reduce) {                 // a spark travelling subject → object
        const u = (t * 0.45 + (a.phase % 1)) % 1, q = 1 - u;
        const sx = q * q * a.x + 2 * q * u * mx + u * u * b.x, sy = q * q * a.y + 2 * q * u * my + u * u * b.y;
        c.fillStyle = "#fff"; c.shadowColor = b.colour; c.shadowBlur = 12;
        c.beginPath(); c.arc(sx, sy, 2.2 / Math.sqrt(cam.k), 0, 7); c.fill(); c.shadowBlur = 0;
      }
      if ((pe || fe) && l.label && cam.k > 0.45) {
        c.font = `${11 / Math.sqrt(cam.k)}px Inter, system-ui, sans-serif`; c.textAlign = "center";
        c.fillStyle = "rgba(230,235,255,.85)"; c.fillText(l.label, mx, my);
      }
    }
    // nodes: glowing core, breathing (important ones drawn last, on top; secondary labels skip if they collide)
    const boxes = [];
    const order = [...nodes].sort((a, b) => a.imp - b.imp);
    for (const n of order) {
      if (!visible(n)) continue;
      let born = reveal ? Math.min(1, ((performance.now() - reveal.start) / reveal.dur - n._shown) * 12) : 1;
      if (n.bornAt && !reveal) {                   // live: a new thing pops in (elastic) with a ring and sparks
        const a = (performance.now() - n.bornAt) / 700;
        if (a < 0) continue;
        if (!n.sparked) { n.sparked = true; burst(n); }
        if (a < 1) born = Math.max(0.05, 1 + Math.pow(2, -10 * a) * Math.sin((a - 0.075) * 2 * Math.PI / 0.3));
        if (a < 2.2) {
          const rr = n.r + 6 + a * 34;
          c.strokeStyle = hexA(n.colour, Math.max(0, 0.8 * (1 - a / 2.2))); c.lineWidth = 2 / Math.sqrt(cam.k);
          c.beginPath(); c.arc(n.x, n.y, rr, 0, 7); c.stroke();
        }
      }
      const dim = (onPath && !onPath.has(n.id)) || (focus && n !== focus && !(near && near.has(n.id)) && !onPath);
      const q = $("g-q") && $("g-q").value.trim().toLowerCase();
      const miss = q && !String(n.label).toLowerCase().includes(q);
      const r = n.r * (reduce ? 1 : 1 + 0.06 * Math.sin(t * 1.4 + n.phase)) * (0.4 + 0.6 * born);
      c.globalAlpha = (dim || miss ? 0.18 : 1) * Math.min(1, born);
      c.shadowColor = n.colour; c.shadowBlur = (n === focus ? 34 : 14 + 18 * n.imp);
      const fill = c.createRadialGradient(n.x - r * 0.35, n.y - r * 0.35, r * 0.1, n.x, n.y, r);
      fill.addColorStop(0, "#ffffff"); fill.addColorStop(0.25, n.colour); fill.addColorStop(1, hexA(n.colour, 0.55));
      c.fillStyle = fill; c.beginPath(); c.arc(n.x, n.y, r, 0, 7); c.fill();
      c.shadowBlur = 0;
      if (n.bridge) { c.strokeStyle = "rgba(255,255,255,.9)"; c.lineWidth = 1.6 / Math.sqrt(cam.k); c.beginPath(); c.arc(n.x, n.y, r + 3.5, 0, 7); c.stroke(); }
      if (n === sel || (onPath && onPath.has(n.id))) { c.strokeStyle = "#ffd166"; c.lineWidth = 2.5 / Math.sqrt(cam.k); c.beginPath(); c.arc(n.x, n.y, r + 7, 0, 7); c.stroke(); }
      const show = n.top || n === focus || (near && near.has(n.id)) || (onPath && onPath.has(n.id)) || (q && !miss) || cam.k > 1.6;
      let lab = show;
      const fs = (n.top ? 12.5 + 4 * n.imp : 11) / Math.sqrt(cam.k) * (pres ? 1.25 : 1);
      if (lab) {
        c.font = `${n.top ? 600 : 500} ${fs}px Inter, system-ui, sans-serif`;
        const w = c.measureText(n.label).width, bx = { x0: n.x - w / 2, x1: n.x + w / 2, y0: n.y + r + 2, y1: n.y + r + fs + 4 };
        const important = n.top || n === focus || (onPath && onPath.has(n.id));
        if (!important && boxes.some((b) => b.x0 < bx.x1 && bx.x0 < b.x1 && b.y0 < bx.y1 && bx.y0 < b.y1)) lab = false;
        else boxes.push(bx);
      }
      if (lab) {
        c.textAlign = "center";
        c.lineWidth = 4 / Math.sqrt(cam.k); c.strokeStyle = "rgba(5,6,15,.85)"; c.lineJoin = "round";
        c.strokeText(n.label, n.x, n.y + r + fs + 2); c.fillStyle = dim ? "rgba(220,225,245,.4)" : "#eef1ff";
        c.fillText(n.label, n.x, n.y + r + fs + 2);
      }
      c.globalAlpha = 1;
    }
    drawSparks(c);
    c.restore();
    if (reveal) {                                    // time-lapse caption
      const p = Math.min(1, (performance.now() - reveal.start) / reveal.dur);
      const d = reveal.dates[Math.min(reveal.dates.length - 1, Math.floor(p * reveal.dates.length))];
      c.font = "600 22px Inter, system-ui, sans-serif"; c.textAlign = "left"; c.fillStyle = "rgba(238,241,255,.92)";
      c.fillText(d ? new Date(d).toLocaleDateString(undefined, { month: "long", year: "numeric" }) : "", 24, H - 28);
      c.fillStyle = "rgba(139,108,255,.9)"; c.fillRect(24, H - 18, (W - 48) * p, 3);
      if (p >= 1 && !reveal.hold) { reveal.hold = performance.now(); }
      if (reveal.hold && performance.now() - reveal.hold > 1200) { reveal = null; if (rec) stopRec(); }
    }
    if (pres) {
      c.font = "700 15px Inter, system-ui, sans-serif"; c.textAlign = "right"; c.fillStyle = "rgba(238,241,255,.75)";
      c.fillText(T("Mnemos · your memory, on your computer"), W - 24, H - 24);
    }
  }
  function burst(n) {
    if (reduce) return;
    for (let i = 0; i < 18; i++) {
      const a = Math.random() * 7, v = 1.2 + Math.random() * 3.2;
      sparks.push({ x: n.x, y: n.y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1, c: i % 3 ? n.colour : "#ffffff", s: 1 + Math.random() * 2 });
    }
    if (sparks.length > 600) sparks = sparks.slice(-600);
  }
  function drawSparks(c) {
    if (!sparks.length) return;
    for (const p of sparks) {
      p.x += p.vx; p.y += p.vy; p.vx *= 0.94; p.vy *= 0.94; p.life -= 0.022;
      if (p.life <= 0) continue;
      c.globalAlpha = p.life; c.fillStyle = p.c; c.shadowColor = p.c; c.shadowBlur = 10;
      c.beginPath(); c.arc(p.x, p.y, p.s * p.life / Math.sqrt(cam.k) + 0.4, 0, 7); c.fill();
    }
    c.globalAlpha = 1; c.shadowBlur = 0;
    sparks = sparks.filter((p) => p.life > 0);
  }
  // ------------------------------------------------------------------ live build: progress card on the stage
  const fmtEta = (s) => s == null ? "" : s < 60 ? (ES ? "menos de 1 min" : "under a minute") : s < 3600 ? `~${Math.round(s / 60)} min` : `~${(s / 3600).toFixed(1)} h`;
  function liveMsg() {
    const f = (G && G.found) || [];
    if (!f.length) return IDLE[msgI % IDLE.length];
    const x = f[f.length - 1 - (msgI % Math.min(f.length, 8))];
    return MSGS[msgI % MSGS.length].replace("{a}", x.s).replace("{b}", x.o).replace("{r}", x.r);
  }
  function progressCard() {
    let el = $("g-live");
    const stage = $("g-stage");
    if (!G || !G.building) { if (el) { el.classList.add("out"); setTimeout(() => el && el.remove(), 600); } clearInterval(msgT); msgT = null; return; }
    if (!el) { el = document.createElement("div"); el.id = "g-live"; el.className = "g-live"; stage.appendChild(el); }
    const p = G.progress || {}, pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
    const eng = G.engine ? esc(G.engine) : (ES ? "el modelo local" : "the local model");
    const fresh = (G.found || []).filter((x) => !seenFound.has(x.seq)).length;
    (G.found || []).forEach((x) => seenFound.add(x.seq));
    el.innerHTML = `<div class="g-live-top"><span class="g-orb"></span><b>${ES ? "Descubriendo conexiones" : "Discovering connections"}</b>` +
      `<span class="muted small">${eng}</span>${fresh ? `<span class="g-new">+${fresh}</span>` : ""}</div>` +
      `<div class="g-bar"><i style="width:${Math.max(3, pct)}%"></i></div>` +
      `<div class="g-live-row small"><span>${p.total ? `${p.done}/${p.total} ${ES ? "notas" : "notes"} · ${pct}%` : ""}</span>` +
      `<span>${p.eta != null ? `${fmtEta(p.eta)} ${ES ? "restante" : "left"}` : (ES ? "calculando…" : "estimating…")}</span></div>` +
      `<div class="g-msg" id="g-msg">${esc(liveMsg())}</div>`;
    if (!msgT) msgT = setInterval(() => {
      const m = $("g-msg"); if (!m) return;
      msgI++; m.classList.add("swap"); setTimeout(() => { m.textContent = liveMsg(); m.classList.remove("swap"); }, 250);
    }, 2600);
  }
  function hexA(hex, a) {
    const h = hex.replace("#", ""), v = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
    return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
  }

  // ------------------------------------------------------------------ interaction
  function pick(px, py) {
    const w = toWorld(px, py);
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      const d = Math.hypot(n.x - w.x, n.y - w.y);
      if (d < n.r + 8 / cam.k && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  function shortest(a, b) {
    const prev = new Map([[a, null]]), q = [a];
    while (q.length) {
      const x = q.shift();
      if (x === b) break;
      for (const y of adj.get(x) || []) if (!prev.has(y)) { prev.set(y, x); q.push(y); }
    }
    if (!prev.has(b)) return null;
    const out = [];
    for (let x = b; x !== null; x = prev.get(x)) out.unshift(x);
    return out;
  }
  function bind() {
    let pan = null, moved = false;
    cv.addEventListener("pointerdown", (ev) => {
      const r = cv.getBoundingClientRect(), n = pick(ev.clientX - r.left, ev.clientY - r.top);
      moved = false; cv.setPointerCapture(ev.pointerId);
      if (n) { const w = toWorld(ev.clientX - r.left, ev.clientY - r.top); drag = { n, last: w }; n.fx = n.x; n.fy = n.y; sim.alpha(Math.max(sim.alpha(), 0.25)); }
      else pan = { x: ev.clientX, y: ev.clientY, cx: cam.x, cy: cam.y };
      target = null;
    });
    cv.addEventListener("pointermove", (ev) => {
      const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
      if (drag) {
        const w = toWorld(px, py), dx = w.x - drag.last.x, dy = w.y - drag.last.y;
        drag.n.fx += dx; drag.n.fy += dy; drag.last = w; moved = true;
        if (!$("g-pull") || $("g-pull").checked) for (const id of adj.get(drag.n.id)) { const m = byId.get(id); m.x += dx * PULL; m.y += dy * PULL; }
        return;
      }
      if (pan) { cam.x = pan.cx - (ev.clientX - pan.x) / cam.k; cam.y = pan.cy - (ev.clientY - pan.y) / cam.k; moved = true; return; }
      const h = pick(px, py); if (h !== hover) { hover = h; cv.style.cursor = h ? "pointer" : "grab"; }
    });
    cv.addEventListener("pointerup", (ev) => {
      if (drag) { const n = drag.n; n.fx = null; n.fy = null; drag = null; if (!moved) select(n, ev.shiftKey || ev.altKey); }
      else if (pan && !moved) { sel = null; path = null; panel(); }
      pan = null;
    });
    cv.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top, before = toWorld(px, py);
      cam.k = Math.min(5, Math.max(0.12, cam.k * Math.exp(-ev.deltaY * 0.0015)));
      const after = toWorld(px, py); cam.x += before.x - after.x; cam.y += before.y - after.y; target = null;
    }, { passive: false });
    cv.addEventListener("dblclick", () => fit(false));
    window.addEventListener("resize", () => { if (running) resize(); });
    document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && pres) setPres(false); setTimeout(() => { resize(); fit(false); }, 60); });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && pres) setPres(false); });
  }
  function select(n, extend) {
    if (extend && sel && sel !== n) { path = shortest(sel.id, n.id); panel(n); return; }
    sel = n; path = null; panel();
    target = { x: n.x, y: n.y, k: Math.max(cam.k, 1.15) };
  }

  // ------------------------------------------------------------------ side panel (subject-first relations, notes)
  function panel(other) {
    const box = $("g-node");
    if (!G) return;
    if (path && other) {
      const names = path.map((id) => esc(byId.get(id).label));
      box.innerHTML = `<h2>${names[0]} → ${names[names.length - 1]}</h2><p class="small muted">How they connect (${path.length - 1} step${path.length > 2 ? "s" : ""}):</p><p>${names.join(' <span class="muted">→</span> ')}</p>`;
      return;
    }
    if (path === null && other) { box.innerHTML = `<p class="muted">No link between ${esc(sel.label)} and ${esc(other.label)} yet.</p>`; return; }
    if (!sel) { summary(); return; }
    const n = sel, name = (x) => (x === n ? `<b class="self">${esc(x.label)}</b>` : `<b>${esc(x.label)}</b>`);
    const mine = links.filter((l) => l.source === n || l.target === n);
    const rel = (l) => `<li>${name(l.source)} — <i>${esc(l.label || "with")}</i> → ${name(l.target)}</li>`;
    const out = mine.filter((l) => l.source === n), inc = mine.filter((l) => l.target === n);
    const named = mine.some((l) => l.label && l.label !== "with");
    if (!named) {
      const nb = [...adj.get(n.id)].map((id) => byId.get(id)).sort((a, b) => b.count - a.count);
      out.length = 0; inc.length = 0;
      n._chips = nb.length ? `<p class="muted small">${esc(T("Often together with"))} (${nb.length})</p><div class="g-chips">${nb.slice(0, 24).map((x) => `<a data-id="${esc(x.id)}" style="border-color:${x.colour}">${esc(x.label)}</a>`).join("")}</div>` : "";
    } else n._chips = "";
    const group = (title, ls) => ls.length ? `<p class="muted small">${title} (${ls.length})</p><ul class="nlist small">${ls.slice(0, 20).map(rel).join("")}</ul>` : "";
    const types = G.edges.filter((e) => e.source === n.id && ["is a", "is_a"].includes(e.label)).map((e) => (G.nodes.find((x) => x.id === e.target) || {}).label).filter(Boolean);
    const sp = (n.spaces || []).map((s) => `<span class="ap-pill">${esc(spaceLabel(s))}</span>`).join(" ");
    box.innerHTML = `<h2>${esc(n.label)}</h2><p class="small muted">${esc(T(TYPE_L[n.type] ? n.type : "thing"))}${types.length ? " · " + esc(T("type:")) + " " + esc(types.join(", ")) : ""} · ${esc(F(n.count > 1 ? "in {n} notes" : "in 1 note", { n: n.count }))} ${sp}${n.bridge ? " · " + esc(T("bridge between spaces")) : ""}</p>` +
      n._chips + group(T("Outgoing"), out) + group(T("Incoming"), inc) +
      `<p class="muted small">${esc(T("The notes behind it"))}</p>` +
      (n.notes || []).map((id) => { const t = G.texts[id] || ""; return `<div class="me-inote"><div>${esc(t.slice(0, 280))}${t.length > 280 ? "…" : ""}</div><a class="small g-open" data-q="${esc(t.slice(0, 60))}">${esc(T("open in Notes →"))}</a></div>`; }).join("") +
      `<p class="small muted">${esc(T("Shift-click another thing to see how they connect."))}</p>`;
  }
  function summary() {
    const how = T(G.source === "model" ? "found by the model" : G.source === "entities" ? "from your memory model" : "names and topics");
    // 0.19: without the private model, names come from simple rules; say so once, with the way to improve it
    const bld = G.basic && nodes.length ? `<p class="small g-basic">${esc(T("Topics named with simple rules, so some may look odd."))} <a href="#settings">${esc(T("Improve my memory"))}</a></p>` : "";
    progressCard();
    const per = G.notes_by_space ? Object.entries(G.notes_by_space).map(([s, k]) => `${esc(spaceLabel(s))} ${k}`).join(" · ") : "";
    $("g-node").innerHTML = (nodes.length ? `<p class="muted">${esc(F(G.notes === 1 ? "{n} {how} from 1 note" : "{n} {how} from {k} notes", { n: nodes.length, how, k: G.notes }))}${per ? ` (${per})` : ""}. ${esc(T("Tap one; shift-click a second to see the path."))}</p>`
      : `<div class="g-empty"><b>${esc(T("Your map is empty for now"))}</b><p class="muted">${esc(T("It fills up by itself as your AIs save what they learn about you."))}</p><a class="mbtn primary" href="#apps">${esc(T("Connect an AI"))}</a></div>`) + bld;
    const top = nodes.filter((n) => n.top).sort((a, b) => b.count - a.count);
    $("g-top").innerHTML = top.length ? `<b class="small">${esc(T("Most important"))}</b><ol class="small">${top.map((n) => `<li><a data-id="${esc(n.id)}">${esc(n.label)}</a> <span class="muted">${n.count}</span></li>`).join("")}</ol>` : "";
  }
  function legend() {
    const by = colourBy();
    let items;
    if (by === "space") items = galaxies.map((g) => [g.colour, g.label]).concat(nodes.some((n) => n.bridge) ? [["#ffffff", T("bridge: in several spaces")]] : []);
    else if (by === "cluster") items = [[SPACE_C[0], T("groups of things linked to each other")]];
    else { const used = new Set(nodes.map((n) => n.type)); items = Object.keys(TYPE_C).filter((t) => used.has(t)).map((t) => [TYPE_C[t], T(TYPE_L[t])]); }
    $("g-legend").innerHTML = items.map(([c, l]) => `<span><i style="background:${c};box-shadow:0 0 8px ${c}"></i>${esc(l)}</span>`).join("") + `<span class="muted">${esc(T("size = importance"))}</span>`;
  }

  // ------------------------------------------------------------------ time-lapse, export, presentation
  function timelapse(ms) {
    const order = [...nodes].sort((a, b) => String(a.first || "9").localeCompare(String(b.first || "9")));
    order.forEach((n, i) => (n._shown = (i / Math.max(1, order.length)) * 0.92));
    sel = null; path = null; reveal = null; target = bounds(); panel();
    reveal = { start: performance.now(), dur: ms || 9000, dates: order.map((n) => n.first).filter(Boolean) };
  }
  function png() {
    cv.toBlob((b) => download(b, "mnemos-graph.png"), "image/png");
  }
  function download(blob, name) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  function record() {
    if (!cv.captureStream || !window.MediaRecorder) { alert("Video recording is not available in this browser"); return; }
    const type = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"].find((t) => MediaRecorder.isTypeSupported(t));
    const chunks = [];
    rec = new MediaRecorder(cv.captureStream(60), { mimeType: type, videoBitsPerSecond: 8_000_000 });
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => { download(new Blob(chunks, { type }), "mnemos-graph." + (type.includes("mp4") ? "mp4" : "webm")); rec = null; $("g-rec").textContent = "● Record"; };
    rec.start(); $("g-rec").textContent = "■ Recording…";
    fit(true); setTimeout(() => timelapse(9000), 1200);
  }
  function stopRec() { if (rec && rec.state === "recording") rec.stop(); }
  function setPres(on) {
    pres = on;
    const wrap = $("g-stage");
    document.body.classList.toggle("g-pres", on);
    if (on && wrap.requestFullscreen) wrap.requestFullscreen().catch(() => {});
    if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    setTimeout(() => { resize(); fit(true); }, 120);
  }

  // ------------------------------------------------------------------ public
  async function load(quiet) {
    quiet = quiet === true;
    if (!cv) {
      cv = $("g-canvas"); ctx = cv.getContext("2d"); bind();
      $("g-top").addEventListener("click", (ev) => { const a = ev.target.closest("a[data-id]"); if (a && byId.get(a.dataset.id)) select(byId.get(a.dataset.id)); });
      $("g-node").addEventListener("click", (ev) => {
        const chip = ev.target.closest(".g-chips a[data-id]");
        if (chip && byId.get(chip.dataset.id)) { select(byId.get(chip.dataset.id)); return; }
        const a = ev.target.closest("a.g-open"); if (!a) return;
        location.hash = "#memoria";
        setTimeout(() => { const q = $("me-q"); if (q) { q.value = a.dataset.q; q.dispatchEvent(new Event("input")); } }, 150);
      });
      $("g-fit").onclick = () => fit(false);
      $("g-grow").onclick = () => timelapse(9000);
      $("g-png").onclick = png;
      $("g-rec").onclick = () => (rec ? stopRec() : record());
      $("g-pres").onclick = () => setPres(!pres);
      $("g-colour").onchange = () => { nodes.forEach((n) => (n.colour = colourOf(n))); legend(); };
      $("g-hideshared").onchange = () => { if (G) { build(G); legend(); summary(); fit(false); } };
      $("g-q").oninput = () => {};
      $("g-ctx").onchange = () => { seenFound = new Set(); nodes = []; links = []; load(); };
      $("g-me").onchange = () => load();
    }
    if (!quiet) $("g-node").innerHTML = '<p class="muted"><span class="spin"></span>reading your notes…</p>';
    let g;
    try { g = await api("/api/graph?" + new URLSearchParams({ context: $("g-ctx").value, me: $("g-me").checked ? "1" : "0" })); }
    catch (e) { if (!quiet) $("g-node").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    const first = !nodes.length;
    if (!quiet) { sel = null; path = null; reveal = null; resize(); }
    const keepSel = sel && sel.id, keepPath = path;
    build(g); legend();
    if (quiet && keepSel && byId.get(keepSel)) { sel = byId.get(keepSel); path = keepPath; } else if (quiet) { sel = null; path = null; }
    if (!quiet || !sel) summary(); else progressCard();
    const shown = !$("view-graph").classList.contains("hidden");
    if (!running && shown) { running = true; requestAnimationFrame(frame); }   // never animate in the background
    if (!quiet) fit(first);
    clearTimeout(load._poll);
    if (g.building && !$("view-graph").classList.contains("hidden")) load._poll = setTimeout(() => load(true), 4000);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && cv && !running && nodes.length && !$("view-graph").classList.contains("hidden")) { running = true; requestAnimationFrame(frame); }
  });
  window.addEventListener("mnemos:view", (ev) => { if (ev.detail !== "graph") { running = false; } else if (cv && !running && nodes.length) { running = true; resize(); requestAnimationFrame(frame); } });
  window.MnemosGraph = { load: () => load(), timelapse, png, fit, _live: (g) => { build(g); legend(); summary(); }, _state: () => ({ nodes: nodes.length, links: links.length, galaxies: galaxies.map((g) => g.name) }) };
})();
