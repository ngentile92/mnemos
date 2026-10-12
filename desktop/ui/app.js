"use strict";
const { invoke } = window.__TAURI__.core;
const $ = (id) => document.getElementById(id);
let last = null;

async function refresh() {
  try { last = await invoke("status"); } catch (e) { $("err").textContent = String(e); return; }
  $("wizard").classList.toggle("hidden", last.initialized);
  $("main").classList.toggle("hidden", !last.initialized);
  const up = last.running && last.healthy;
  $("dot").className = "dot " + (up ? "ok" : last.running ? "" : "bad");
  $("state").textContent = up ? "Mnemos is running" : last.running ? "Starting…" : "Mnemos is stopped";
  $("where").textContent = `Your memory lives in ${last.home}`;
  $("start").disabled = last.running; $("stop").disabled = !last.running; $("open").disabled = !up;
  const box = $("connect");
  if (box.dataset.ctx !== last.contexts.join(",")) {
    box.dataset.ctx = last.contexts.join(",");
    box.replaceChildren(...last.contexts.map((c) => {
      const b = document.createElement("button");
      b.textContent = `Connect “${c}”`;
      b.onclick = (e) => run(e.target, async () => { $("ok").textContent = await invoke("connect_claude", { context: c }); });
      return b;
    }));
  }
  $("details").textContent = JSON.stringify(last, null, 1);
}

async function run(btn, fn) {
  btn.disabled = true; $("err").textContent = ""; $("ok").textContent = "";
  try { await fn(); } catch (e) { $("err").textContent = String(e); }
  await refresh();
}

$("start").onclick = (e) => run(e.target, () => invoke("start_hub"));
$("stop").onclick = (e) => run(e.target, () => invoke("stop_hub"));
$("open").onclick = () => window.__TAURI__.opener.openUrl(last.url);
$("w-go").onclick = async (e) => {
  const pw = $("w-pw").value;
  $("w-err").textContent = "";
  if (pw.length < 8) return ($("w-err").textContent = "Password: at least 8 characters.");
  if (pw !== $("w-pw2").value) return ($("w-err").textContent = "Passwords do not match.");
  e.target.disabled = true; e.target.textContent = "Creating… ";
  try {
    await invoke("setup", { contexts: $("w-ctx").value.split(","), owner: $("w-owner").value, password: pw });
    $("w-pw").value = $("w-pw2").value = "";
    await invoke("start_hub");
  } catch (err) { $("w-err").textContent = String(err); }
  e.target.disabled = false; e.target.textContent = "Create my Mnemos";
  await refresh();
};
refresh();
setInterval(refresh, 2000);
