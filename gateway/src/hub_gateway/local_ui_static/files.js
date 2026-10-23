"use strict";
// Simple memory (0.11): the Memory screen as files (one folder per space) plus the migration card.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("files-view")) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = (path, body) => window.MNEMOS_API(path, body);
  let spaces = [], open = null, version = null;

  async function status() {
    const st = await api("/api/memory-migrate");
    $("files-view").classList.toggle("hidden", !st.active);
    $("mig-card").classList.toggle("hidden", st.active);
    document.body.classList.toggle("filesmode", st.active);
    if (st.active) loadTopics();
    return st;
  }
  async function loadTopics() {
    const ctx = ($("me-ctx") && $("me-ctx").value) || (window.MNEMOS.contexts[0] || {}).name || "";
    const j = await api("/api/files?context=" + encodeURIComponent(ctx));
    if (j.error) { $("fv-list").innerHTML = `<p class="muted">${esc(j.error)}</p>`; return; }
    spaces = j.spaces || [];
    const n = spaces.reduce((a, s) => a + s.topics.reduce((b, t) => b + t.notes, 0), 0);
    $("fv-head").textContent = n + " notes";
    $("fv-list").innerHTML = spaces.map((s) => `<h3 class="small" style="margin:10px 0 4px">${esc(s.space)}${s.editable ? "" : " · read-only"}</h3>` +
      s.topics.map((t) => `<div class="me-item" data-file="${esc(t.file)}"><div class="m"><span>${esc(t.title)}</span><span>${t.notes} notes${t.long ? " · long" : ""}</span></div><div>${esc(t.first || "")}</div></div>`).join("")).join("");
    $("fv-list").querySelectorAll("[data-file]").forEach((el) => { el.onclick = () => openFile(el.dataset.file); });
  }
  async function openFile(rel) {
    const ctx = ($("me-ctx") && $("me-ctx").value) || "";
    const j = await api("/api/files?context=" + encodeURIComponent(ctx) + "&op=read&file=" + encodeURIComponent(rel));
    if (j.error) { $("fv-msg").textContent = j.error; return; }
    open = rel; version = j.version;
    $("fv-list").classList.add("hidden"); $("fv-editor").classList.remove("hidden");
    $("fv-title").textContent = rel; $("fv-meta").textContent = "version " + j.version;
    $("fv-text").value = j.text; $("fv-hist").innerHTML = ""; $("fv-msg").textContent = "";
    const space = spaces.find((s) => rel.startsWith(s.space + "/"));
    $("fv-save").disabled = !(space && space.editable);
  }
  $("fv-back").onclick = () => { $("fv-editor").classList.add("hidden"); $("fv-list").classList.remove("hidden"); loadTopics(); };
  $("fv-save").onclick = async () => {
    const ctx = $("me-ctx").value;
    try {
      const j = await api("/api/files", { context: ctx, op: "write", file: open, text: $("fv-text").value, version });
      if (j.error) { $("fv-msg").textContent = j.error; return; }
      version = j.version; $("fv-meta").textContent = "version " + j.version; $("fv-msg").textContent = "Saved ✓";
    } catch (e) { $("fv-msg").textContent = e.message; }
  };
  $("fv-history").onclick = async () => {
    const ctx = $("me-ctx").value;
    const j = await api("/api/files?context=" + encodeURIComponent(ctx) + "&op=history&file=" + encodeURIComponent(open));
    $("fv-hist").innerHTML = (j.history || []).map((h) => `<div class="me-item"><div class="m"><span>${esc(new Date(h.at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }))}</span><span>${esc(h.author || "")}</span></div><div>${esc(h.message || "")} <button class="mbtn fv-restore" data-sha="${esc(h.sha)}">Restore</button></div></div>`).join("") || "<p class='muted small'>No history yet.</p>";
    $("fv-hist").querySelectorAll(".fv-restore").forEach((b) => { b.onclick = async () => {
      const r = await api("/api/files", { context: ctx, op: "restore", file: open, sha: b.dataset.sha });
      if (r.error) { $("fv-msg").textContent = r.error; return; }
      openFile(open);
    }; });
  };
  $("mig-preview").onclick = async () => {
    $("mig-msg").textContent = "Preparing the preview…";
    try {
      const j = await api("/api/memory-migrate", { action: "preview" });
      $("mig-report").classList.remove("hidden");
      $("mig-report").textContent = Object.entries(j.files || {}).map(([f, n]) => f + ": " + n + " notes").join("\n") +
        "\n\n" + j.notes + " notes, " + j.past + " kept as history. Nothing was changed; the preview is in migration-preview/.";
      $("mig-msg").textContent = "Preview ready ✓";
    } catch (e) { $("mig-msg").textContent = e.message; }
  };
  $("mig-apply").onclick = async () => {
    if (!confirm("Switch this memory to files? You can switch back afterwards, and your current notes stay where they are.")) return;
    $("mig-msg").textContent = "Switching…";
    try {
      const j = await api("/api/memory-migrate", { action: "apply" });
      if (j.error) { $("mig-msg").textContent = j.error; return; }
      window.MNEMOS.store = "files";
      $("mig-msg").textContent = "Done ✓ " + j.notes + " notes in " + Object.keys(j.files || {}).length + " files. Reload to see them.";
      status();
    } catch (e) { $("mig-msg").textContent = e.message; }
  };
  async function settings() {
    const st = await api("/api/memory-migrate");
    $("sm-state").textContent = st.active ? "on" : "off";
    $("sm-undo").classList.toggle("hidden", !(st.active && st.applied));
  }
  $("sm-undo").onclick = async () => {
    if (!confirm("Go back to the previous memory store? Notes added or edited since the switch are copied back first.")) return;
    try { const j = await api("/api/memory-migrate", { action: "undo" }); $("sm-msg").textContent = j.error || "Back on the previous store ✓ Reload Mnemos."; window.MNEMOS.store = "notes"; settings(); }
    catch (e) { $("sm-msg").textContent = e.message; }
  };
  window.addEventListener("mnemos:memory-changed", () => { if (!$("files-view").classList.contains("hidden") && $("fv-editor").classList.contains("hidden")) loadTopics(); });
  $("me-ctx") && $("me-ctx").addEventListener("change", () => { if (!$("files-view").classList.contains("hidden")) loadTopics(); });
  window.addEventListener("mnemos:view", (e) => { if (e.detail === "memoria") status(); if (e.detail === "settings") settings(); });
})();
