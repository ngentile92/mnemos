// Mnemos Desktop (MVP scaffold): starts/stops the local hub (`mnemos-local serve`), shows its status,
// opens its page, and runs the first-run wizard (contexts + password). Unsigned; see desktop/README.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use tauri::{Manager, RunEvent, State};

const PORT: u16 = 8765;
// Release builds ship a relocatable Python with mnemos-hub preinstalled in Resources/python
// (desktop/scripts/bundle_python.sh, CI). Dev builds without it fall back to uv.
static BUNDLED_PY: OnceLock<Option<PathBuf>> = OnceLock::new();
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
    contexts: Vec<String>,
}

/// Context names from contexts.yaml (the `  name:` keys under `contexts:`).
fn contexts() -> Vec<String> {
    let text = std::fs::read_to_string(home().join("contexts.yaml")).unwrap_or_default();
    let mut inside = false;
    let mut out = vec![];
    for line in text.lines() {
        if line.starts_with("contexts:") {
            inside = true;
            continue;
        }
        if inside && !line.is_empty() && !line.starts_with(' ') {
            inside = false;
        }
        if inside && line.starts_with("  ") && !line.starts_with("   ") {
            if let Some(name) = line.trim().strip_suffix(':') {
                if valid_ctx(name) {
                    out.push(name.to_string());
                }
            }
        }
    }
    out
}

fn claude_config_path() -> PathBuf {
    let h = PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into()));
    if let Ok(p) = std::env::var("MNEMOS_CLAUDE_CONFIG") {
        return PathBuf::from(p);
    }
    if cfg!(target_os = "macos") {
        h.join("Library/Application Support/Claude/claude_desktop_config.json")
    } else if cfg!(windows) {
        PathBuf::from(std::env::var("APPDATA").unwrap_or_default()).join("Claude/claude_desktop_config.json")
    } else {
        h.join(".config/Claude/claude_desktop_config.json")
    }
}

/// Add (or replace) a `mnemos-<ctx>` stdio server in Claude Desktop's config, backing the file up first.
/// stdio needs no URL or token: Claude Desktop launches this app's own runtime directly.
#[tauri::command]
fn connect_claude(context: String) -> Result<String, String> {
    if !contexts().contains(&context) {
        return Err(format!("unknown context: {context}"));
    }
    let (prog, mut args) = hub_program()?;
    args.extend(["stdio", "--home", &home().display().to_string(), "--context", &context].map(String::from));
    let path = claude_config_path();
    let mut cfg: serde_json::Value = match std::fs::read_to_string(&path) {
        Ok(t) if !t.trim().is_empty() => {
            serde_json::from_str(&t).map_err(|e| format!("Claude config is not valid JSON ({e}); not touching it"))?
        }
        _ => serde_json::json!({}),
    };
    if !cfg.is_object() {
        return Err("Claude config is not a JSON object; not touching it".into());
    }
    if path.exists() {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        std::fs::copy(&path, path.with_extension(format!("json.mnemos-backup-{stamp}"))).map_err(|e| e.to_string())?;
    } else if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let servers = cfg.as_object_mut().unwrap().entry("mcpServers").or_insert_with(|| serde_json::json!({}));
    if !servers.is_object() {
        return Err("mcpServers in the Claude config is not an object; not touching it".into());
    }
    servers.as_object_mut().unwrap().insert(format!("mnemos-{context}"), serde_json::json!({"command": prog, "args": args}));
    let tmp = path.with_extension("json.mnemos-tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&cfg).unwrap() + "\n").map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    Ok(format!("Added mnemos-{context} to Claude Desktop. Quit and reopen Claude Desktop to use it."))
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

fn bundled_python() -> Option<PathBuf> {
    BUNDLED_PY.get().cloned().flatten()
}

/// (program, args before the subcommand) to run mnemos-local: dev override, bundled Python, then uv.
fn hub_program() -> Result<(String, Vec<String>), String> {
    if let Ok(c) = std::env::var("MNEMOS_HUB_CMD") {
        let mut parts = c.split_whitespace().map(String::from);
        let prog = parts.next().ok_or("empty MNEMOS_HUB_CMD")?;
        return Ok((prog, parts.collect()));
    }
    if let Some(py) = bundled_python() {
        return Ok((py.display().to_string(), vec!["-I".into(), "-m".into(), "hub_gateway.local".into()]));
    }
    let uv = find_uv().ok_or("Mnemos runtime missing: reinstall Mnemos Desktop (dev builds need uv)")?;
    Ok((uv, ["tool", "run", "--from", HUB_SPEC, "mnemos-local"].iter().map(|s| s.to_string()).collect()))
}

fn hub_command(args: &[&str]) -> Result<Command, String> {
    let (prog, pre) = hub_program()?;
    let mut cmd = Command::new(prog);
    cmd.args(pre).args(args);
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
        runtime: hub_program().ok().map(|(p, _)| p),
        contexts: contexts(),
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

/// The local app with a one-time unlock: the token travels in the URL fragment (never sent to a server log)
/// and the page swaps it for a session cookie.
#[tauri::command]
fn memory_url() -> Result<String, String> {
    let tok = std::fs::read_to_string(home().join("token")).map_err(|_| "Mnemos is not set up yet".to_string())?;
    Ok(format!("http://127.0.0.1:{PORT}/app#k={}", tok.trim()))
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
        .setup(|app| {
            let py = app.path().resource_dir().ok().map(|r| r.join("python/bin/python3")).filter(|p| p.is_file());
            let _ = BUNDLED_PY.set(py);
            // MNEMOS_AUTOSTART=1: start the hub on launch (used by the CI smoke test of the packaged app)
            if std::env::var("MNEMOS_AUTOSTART").as_deref() == Ok("1") && home().join("contexts.yaml").exists() {
                if let Err(e) = start_hub(app.state::<Hub>()) {
                    eprintln!("autostart failed: {e}");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![status, start_hub, stop_hub, setup, connect_claude, memory_url])
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
