"use strict";
// Tarjeta "Add a context": corre scripts/mnemos_context.py add en el servidor (solo archivos) y muestra los pasos.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("ctx-add-card")) return;
  async function run(dry) {
    const out = $("ca-out"); out.classList.remove("hidden"); out.textContent = dry ? "previewing…" : "creating…";
    const projects = $("ca-proj").value.split(",").map((x) => x.trim()).filter(Boolean);
    try {
      const r = await fetch("/api/contexts/add", { method: "POST", headers: { "Content-Type": "application/json", "X-Mnemos-Write": "1" },
        body: JSON.stringify({ name: $("ca-name").value.trim(), description: $("ca-desc").value, projects, dry_run: dry }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
      out.textContent = j.output;
    } catch (e) { out.textContent = "error: " + e.message; }
  }
  $("ca-preview").addEventListener("click", () => run(true));
  $("ca-create").addEventListener("click", () => {
    if (confirm(`Create context "${$("ca-name").value.trim()}"? It edits config/contexts.yaml and .env (backups are kept). No containers are started.`)) run(false);
  });
})();
