// Mnemos Desktop (MVP scaffold): starts/stops the local hub (`mnemos-local serve`), shows its status,
// opens its page, and runs the first-run wizard (contexts + password). Unsigned; see desktop/README.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, RunEvent, State};

const PORT: u16 = 8765;
// Phase 1 replaces this with a bundled sidecar; for the MVP the hub is installed on demand by uv.
const HUB_SPEC: &str = "mnemos-hub @ git+https://github.com/ngentile92/mnemos@main#subdirectory=gateway";

struct Hub(Mutex<Option<Child>>);

#[derive(Serialize)]
struct Status {
    initialized: bool,
    running: bool,
    healthy: bool,
    home: String,
    url: String,
    runtime: Option<String>,
}

fn home() -> PathBuf {
    if let Ok(h) = std::env::var("MNEMOS_HOME") {
        return PathBuf::from(h);
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into())).join("Mnemos")
}

/// GUI apps on macOS do not get the shell PATH: look in the usual places.
fn find_uv() -> Option<String> {
    let h = std::env::var("HOME").unwrap_or_default();
    let mut c = vec![format!("{h}/.local/bin/uv"), "/opt/homebrew/bin/uv".into(), "/usr/local/bin/uv".into()];
    if let Ok(path) = std::env::var("PATH") {
        c.extend(path.split(':').map(|p| format!("{p}/uv")));
    }
    c.into_iter().find(|p| std::path::Path::new(p).is_file())
}

fn hub_command(args: &[&str]) -> Result<Command, String> {
    // MNEMOS_HUB_CMD (dev): e.g. "/path/to/venv/bin/mnemos-local"
    let mut cmd = if let Ok(c) = std::env::var("MNEMOS_HUB_CMD") {
        let mut parts = c.split_whitespace();
        let mut cmd = Command::new(parts.next().ok_or("empty MNEMOS_HUB_CMD")?);
        cmd.args(parts);
        cmd
    } else {
        let uv = find_uv().ok_or("uv not found: install it from https://docs.astral.sh/uv/ (Phase 1 bundles it)")?;
        let mut cmd = Command::new(uv);
        cmd.args(["tool", "run", "--from", HUB_SPEC, "mnemos-local"]);
        cmd
    };
    cmd.args(args);
    Ok(cmd)
}

fn healthy() -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], PORT).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn valid_ctx(c: &str) -> bool {
    let mut ch = c.chars();
    matches!(ch.next(), Some(f) if f.is_ascii_lowercase())
        && c.len() <= 31
        && c.chars().all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '-')
}

#[tauri::command]
fn status(hub: State<Hub>) -> Status {
    let mut g = hub.0.lock().unwrap();
    let running = match g.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(None)),
        None => false,
    };
    if !running {
        *g = None;
    }
    Status {
        initialized: home().join("contexts.yaml").exists(),
        running,
        healthy: healthy(),
        home: home().display().to_string(),
        url: format!("http://127.0.0.1:{PORT}/"),
        runtime: std::env::var("MNEMOS_HUB_CMD").ok().or_else(find_uv),
    }
}

#[tauri::command]
fn start_hub(hub: State<Hub>) -> Result<(), String> {
    let mut g = hub.0.lock().unwrap();
    if let Some(child) = g.as_mut() {
        if matches!(child.try_wait(), Ok(None)) {
            return Ok(());
        }
    }
    if healthy() {
        return Err(format!("port {PORT} is already in use (another Mnemos running?)"));
    }
    let h = home();
    let log = std::fs::File::create(h.join("hub.log")).map_err(|e| e.to_string())?;
    let child = hub_command(&["serve", "--home", &h.display().to_string(), "--port", &PORT.to_string()])?
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
        .map_err(|e| format!("could not start the hub: {e}"))?;
    *g = Some(child);
    Ok(())
}

#[tauri::command]
fn stop_hub(hub: State<Hub>) -> Result<(), String> {
    if let Some(mut child) = hub.0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    Ok(())
}

#[tauri::command]
fn setup(contexts: Vec<String>, owner: String, password: String) -> Result<String, String> {
    let ctxs: Vec<String> = contexts.iter().map(|c| c.trim().to_lowercase()).filter(|c| !c.is_empty()).collect();
    if ctxs.is_empty() || !ctxs.iter().all(|c| valid_ctx(c)) {
        return Err("contexts: lowercase letters, digits and dashes (e.g. personal, work)".into());
    }
    if password.chars().count() < 8 {
        return Err("password: at least 8 characters".into());
    }
    let h = home().display().to_string();
    let owner = if owner.trim().is_empty() { "me".to_string() } else { owner.trim().to_string() };
    let out = hub_command(&["init", "--home", &h, "--contexts", &ctxs.join(","), "--owner", &owner])?
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).lines().last().unwrap_or("init failed").to_string());
    }
    let mut child = hub_command(&["set-password", "--home", &h])?
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    child.stdin.take().ok_or("no stdin")?.write_all(format!("{password}\n").as_bytes()).map_err(|e| e.to_string())?;
    let res = child.wait_with_output().map_err(|e| e.to_string())?;
    if !res.status.success() {
        return Err(String::from_utf8_lossy(&res.stderr).lines().last().unwrap_or("password failed").to_string());
    }
    Ok(h)
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Hub(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![status, start_hub, stop_hub, setup])
        .build(tauri::generate_context!())
        .expect("error while building Mnemos Desktop");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // never leave the hub running after quitting the app
            if let Some(mut child) = handle.state::<Hub>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
