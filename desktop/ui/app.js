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
  $("details").textContent = JSON.stringify(last, null, 1);
}

async function run(btn, fn) {
  btn.disabled = true; $("err").textContent = ""; $("ok").textContent = "";
  try { await fn(); } catch (e) { $("err").textContent = String(e); }
  await refresh();
}

$("connect").onclick = (e) => run(e.target, async () => { $("ok").textContent = await invoke("connect_claude"); });
$("start").onclick = (e) => run(e.target, () => invoke("start_hub"));
$("stop").onclick = (e) => run(e.target, () => invoke("stop_hub"));
$("open").onclick = (e) => run(e.target, async () => window.__TAURI__.opener.openUrl(await invoke("memory_url")));
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
$("login").onchange = async (e) => { try { e.target.checked = await invoke("set_login_item", { enabled: e.target.checked }); } catch (err) { $("err").textContent = String(err); } };
invoke("login_item").then((v) => { $("login").checked = v; });
refresh();
setInterval(refresh, 2000);
