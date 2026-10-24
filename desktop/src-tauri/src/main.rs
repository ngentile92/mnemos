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
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, RunEvent, State, WindowEvent};

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

/// Is this Claude config entry one Mnemos Desktop wrote (an older per-context `mnemos-<ctx>` entry)?
fn is_our_entry(v: &serde_json::Value) -> bool {
    let args = v.get("args").and_then(|a| a.as_array()).cloned().unwrap_or_default();
    let words: Vec<&str> = args.iter().filter_map(|a| a.as_str()).collect();
    words.contains(&"stdio") && words.iter().any(|w| *w == "hub_gateway.local" || *w == "mnemos-local")
}

/// Write ONE `mnemos` stdio server into Claude Desktop's config (every space; Claude uses its default space and
/// can pass `context`), backing the file up first. Older `mnemos-<ctx>` entries written by this app are removed,
/// so Claude does not see the same memory twice. stdio needs no URL or token: Claude launches the app's runtime.
#[tauri::command]
fn connect_claude() -> Result<String, String> {
    let (prog, mut args) = hub_program()?;
    args.extend(["stdio", "--home", &home().display().to_string()].map(String::from));
    let path = claude_config_path();
    let mut cfg: serde_json::Value = match std::fs::read_to_string(&path) {
        Ok(t) if !t.trim().is_empty() => {
            serde_json::from_str(&t).map_err(|e| format!("Claude's settings file is not valid ({e}); not touching it"))?
        }
        _ => serde_json::json!({}),
    };
    if !cfg.is_object() {
        return Err("Claude's settings file has an unexpected format; not touching it".into());
    }
    if path.exists() {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        std::fs::copy(&path, path.with_extension(format!("json.mnemos-backup-{stamp}"))).map_err(|e| e.to_string())?;
    } else if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let servers = cfg.as_object_mut().unwrap().entry("mcpServers").or_insert_with(|| serde_json::json!({}));
    if !servers.is_object() {
        return Err("Claude's settings file has an unexpected format; not touching it".into());
    }
    let map = servers.as_object_mut().unwrap();
    let old: Vec<String> = map
        .iter()
        .filter(|(k, v)| k.starts_with("mnemos-") && is_our_entry(v))
        .map(|(k, _)| k.clone())
        .collect();
    for k in &old {
        map.remove(k);
    }
    map.insert("mnemos".into(), serde_json::json!({"command": prog, "args": args}));
    let tmp = path.with_extension("json.mnemos-tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&cfg).unwrap() + "\n").map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    let moved = if old.is_empty() { String::new() } else { format!(" Replaced the older entries: {}.", old.join(", ")) };
    let dup = if claude_extensions().is_empty() { String::new() } else {
        " Also remove the Mnemos extension in Claude → Settings → Extensions: with both, Claude sees two memories.".to_string()
    };
    Ok(format!("Added Mnemos to Claude Desktop (all your spaces).{moved}{dup} Quit and reopen Claude Desktop to use it."))
}

/// Mnemos extensions (.mcpb) installed in Claude Desktop. With the `mnemos` config entry as well, Claude sees two
/// memories (an old extension may even run an older Mnemos), so the app asks the user to remove the extension.
fn claude_extensions() -> Vec<String> {
    let dir = claude_config_path().with_file_name("Claude Extensions");
    std::fs::read_dir(dir)
        .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| n.to_lowercase().contains("mnemos")).collect())
        .unwrap_or_default()
}

/// Claude Desktop does not pass MCP server instructions to the model, so the save rules only reach it through
/// tool descriptions. Its personal preferences (Settings → Profile) are read in every chat: this text goes there.
const CLAUDE_PREFS: &str = "I use Mnemos as my long-term memory. At the start of every chat call memory_start (memory_profile on older setups). \
Whenever I share something lasting about myself (likes, people, work, projects, decisions, goals), save it right away \
with memory_remember (memory_save on older setups) in one short sentence, without asking. Search it with memory_search when my past or preferences matter.";

/// Opens Claude Desktop so the user can remove an old Mnemos extension (Settings → Extensions). Mnemos never edits
/// Claude's extension files itself.
#[tauri::command]
fn open_claude() -> Result<(), String> {
    std::process::Command::new("/usr/bin/open").args(["-a", "Claude"]).status().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn claude_notes() -> serde_json::Value {
    let ext = claude_extensions();
    serde_json::json!({"extensions": ext, "duplicate": !ext.is_empty() && json_has_server(claude_config_path(), "mcpServers"),
                       "preferences": CLAUDE_PREFS})
}

/// The stdio command every local AI app runs: (program, args). Same runtime Claude Desktop uses.
fn stdio_cmd() -> Result<(String, Vec<String>), String> {
    let (prog, mut args) = hub_program()?;
    args.extend(["stdio", "--home", &home().display().to_string()].map(String::from));
    Ok((prog, args))
}

fn shell_quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "/._-=:@+,".contains(c)) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', r"'\''"))
    }
}

fn b64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(T[((n >> (18 - 6 * i)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

fn pct(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

#[derive(Serialize)]
struct AppCard {
    id: &'static str,
    name: &'static str,
    installed: bool,
    connected: bool,
    how: &'static str,     // "button" | "deeplink" | "command" | "remote"
    command: Option<String>,
    seen: bool,            // the app already talked to Mnemos (a real MCP handshake, from data/apps.json)
}

/// Apps that completed an MCP handshake with this Mnemos (data/apps.json, written by the hub on `initialize`).
fn seen_apps() -> Vec<String> {
    std::fs::read_to_string(home().join("data/apps.json")).ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v.get("apps").and_then(|a| a.as_object()).map(|m| m.iter()
            .filter(|(_, r)| r.get("last_seen").map(|x| !x.is_null()).unwrap_or(false)
                && !r.get("revoked").and_then(|x| x.as_bool()).unwrap_or(false))
            .map(|(k, _)| k.clone()).collect()))
        .unwrap_or_default()
}

/// Full path of a command-line tool (GUI apps do not get the shell PATH).
fn find_bin(bin: &str) -> Option<String> {
    let h = std::env::var("HOME").unwrap_or_default();
    let mut dirs = vec![format!("{h}/.local/bin"), "/opt/homebrew/bin".into(), "/usr/local/bin".into(), format!("{h}/.npm-global/bin"),
                        "/Applications/Visual Studio Code.app/Contents/Resources/app/bin".into()];
    if let Ok(p) = std::env::var("PATH") {
        dirs.extend(p.split(':').map(String::from));
    }
    dirs.iter().map(|d| format!("{d}/{bin}")).find(|p| std::path::Path::new(p).is_file())
}

/// One click for every local app: Claude Desktop (config entry), Cursor (install link) and the command-line apps
/// (Claude Code, Codex, VS Code: Mnemos runs their own `mcp add` for you, so nobody needs Terminal).
#[tauri::command]
fn connect_app(id: String) -> Result<String, String> {
    match id.as_str() {
        "claude-desktop" => connect_claude(),
        "cursor" => connect_cursor(),
        "claude-code" | "codex" | "vscode" => {
            let (prog, args) = stdio_cmd()?;
            let (bin, name) = match id.as_str() { "claude-code" => ("claude", "Claude Code"), "codex" => ("codex", "Codex"), _ => ("code", "VS Code") };
            let exe = find_bin(bin).ok_or_else(|| format!("Could not find {name}'s `{bin}` command on this Mac. Open {name} once (or reinstall it), then try again."))?;
            let mut cmd = Command::new(&exe);
            match id.as_str() {
                "claude-code" => { cmd.args(["mcp", "add", "--scope", "user", "mnemos", "--"]).arg(&prog).args(&args); }
                "codex" => { cmd.args(["mcp", "add", "mnemos", "--"]).arg(&prog).args(&args); }
                _ => { cmd.arg("--add-mcp").arg(serde_json::json!({"name": "mnemos", "command": prog, "args": args}).to_string()); }
            }
            let out = cmd.stdin(Stdio::null()).output().map_err(|e| format!("Could not run {name}: {e}"))?;
            let text = String::from_utf8_lossy(&out.stderr).to_string() + &String::from_utf8_lossy(&out.stdout);
            if !out.status.success() && !text.to_lowercase().contains("already exists") {
                return Err(format!("{name} did not accept Mnemos: {}", text.trim().chars().take(300).collect::<String>()));
            }
            Ok(format!("Added Mnemos to {name}. Restart {name} (or start a new session) and send it any message."))
        }
        _ => Err(format!("unknown app {id}")),
    }
}

fn file_has(p: PathBuf, needle: &str) -> bool {
    std::fs::read_to_string(p).map(|t| t.contains(needle)).unwrap_or(false)
}

fn json_has_server(p: PathBuf, key: &str) -> bool {
    std::fs::read_to_string(p).ok().and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .map(|v| v.get(key).and_then(|m| m.get("mnemos")).is_some()).unwrap_or(false)
}

fn on_path(bin: &str) -> bool {
    let h = std::env::var("HOME").unwrap_or_default();
    let mut dirs = vec![format!("{h}/.local/bin"), "/opt/homebrew/bin".into(), "/usr/local/bin".into(), format!("{h}/.npm-global/bin")];
    if let Ok(p) = std::env::var("PATH") {
        dirs.extend(p.split(':').map(String::from));
    }
    dirs.iter().any(|d| std::path::Path::new(&format!("{d}/{bin}")).is_file())
}

/// One card per AI app: is it on this Mac, is Mnemos already in its config, and how to connect it.
#[tauri::command]
fn app_cards() -> Result<Vec<AppCard>, String> {
    let h = PathBuf::from(std::env::var("HOME").unwrap_or_default());
    let (prog, args) = stdio_cmd()?;
    let line = std::iter::once(prog.as_str()).chain(args.iter().map(|s| s.as_str())).map(shell_quote).collect::<Vec<_>>().join(" ");
    let vs = serde_json::json!({"name": "mnemos", "command": prog, "args": args}).to_string();
    let app = |n: &str| std::path::Path::new(&format!("/Applications/{n}.app")).exists() || h.join(format!("Applications/{n}.app")).exists();
    let vs_user = h.join("Library/Application Support/Code/User/mcp.json");
    let seen = seen_apps();
    let mut cards = vec![
        AppCard { id: "claude-desktop", name: "Claude Desktop", installed: app("Claude") || claude_config_path().exists(),
                  connected: json_has_server(claude_config_path(), "mcpServers"), how: "button", command: None, seen: false },
        AppCard { id: "cursor", name: "Cursor", installed: app("Cursor") || h.join(".cursor").exists(),
                  connected: json_has_server(h.join(".cursor/mcp.json"), "mcpServers"), how: "deeplink", command: None, seen: false },
        AppCard { id: "claude-code", name: "Claude Code", installed: on_path("claude") || h.join(".claude.json").exists(),
                  connected: json_has_server(h.join(".claude.json"), "mcpServers"), how: "command",
                  command: Some(format!("claude mcp add --scope user mnemos -- {line}")), seen: false },
        AppCard { id: "codex", name: "Codex", installed: on_path("codex") || h.join(".codex").exists(),
                  connected: file_has(h.join(".codex/config.toml"), "[mcp_servers.mnemos]"), how: "command",
                  command: Some(format!("codex mcp add mnemos -- {line}")), seen: false },
        AppCard { id: "vscode", name: "VS Code", installed: app("Visual Studio Code") || on_path("code"),
                  connected: json_has_server(vs_user, "servers"), how: "command",
                  command: Some(format!("code --add-mcp {}", shell_quote(&vs))), seen: false },
    ];
    for c in cards.iter_mut() {
        c.seen = seen.iter().any(|k| k == c.id);
    }
    Ok(cards)
}

/// Cursor's one-click install link (cursor.com/docs/mcp/install-links): Cursor asks the user to confirm.
fn cursor_link() -> Result<String, String> {
    let (prog, args) = stdio_cmd()?;
    let cfg = serde_json::json!({"command": prog, "args": args}).to_string();
    Ok(format!("cursor://anysphere.cursor-deeplink/mcp/install?name=mnemos&config={}", pct(&b64(cfg.as_bytes()))))
}

#[tauri::command]
fn connect_cursor() -> Result<String, String> {
    let link = cursor_link()?;
    Command::new("open").arg(&link).status().map_err(|e| format!("could not open Cursor: {e}"))?;
    Ok("Cursor opened: click Install there. Mnemos then appears in Cursor's list of tools (all your spaces).".into())
}

fn home() -> PathBuf {
    if let Ok(h) = std::env::var("MNEMOS_HOME") {
        return PathBuf::from(h);
    }
    if std::env::var("MNEMOS_DEMO").as_deref() == Ok("1") {
        // fictional demo memory for screenshots and talks (seeded by the hub on first start); never ~/Mnemos
        return PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into())).join("Mnemos-demo");
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into())).join("Mnemos")
}

/// The local bearer token: a 0600 file in ~/Mnemos (0.12.1). It only unlocks the local page on 127.0.0.1, so the
/// Keychain bought nothing but password prompts: the app is ad-hoc signed, its signature changes with every build
/// and macOS forgets "Always Allow" on each update (2-4 prompts per start). No Keychain access at all now; an old
/// Keychain item is simply ignored (a fresh token only affects the local page; Claude uses stdio, no token).
static TOKEN: OnceLock<String> = OnceLock::new();

fn token() -> Result<String, String> {
    if let Some(t) = TOKEN.get() {
        return Ok(t.clone());
    }
    let file = home().join("token");
    let t = match std::fs::read_to_string(&file).map(|t| t.trim().to_string()) {
        Ok(t) if !t.is_empty() => t,
        _ => {
            if !home().join("contexts.yaml").exists() {
                return Err("Mnemos is not set up yet".into());
            }
            let t: String = std::iter::repeat_with(|| format!("{:08x}", rand_u32())).take(6).collect();
            write_private(&file, &t)?;
            t
        }
    };
    Ok(TOKEN.get_or_init(|| t).clone())
}

fn write_private(path: &std::path::Path, text: &str) -> Result<(), String> {
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    o.open(path).and_then(|mut f| f.write_all(text.as_bytes())).map_err(|e| e.to_string())
}

fn rand_u32() -> u32 {
    let mut b = [0u8; 4];
    let _ = std::fs::File::open("/dev/urandom").and_then(|mut f| std::io::Read::read_exact(&mut f, &mut b));
    u32::from_le_bytes(b)
}

fn login_plist() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/LaunchAgents/io.mnemos.desktop.plist")
}

/// Start Mnemos when you log in (a LaunchAgent that opens the app in the background, menu bar only).
#[tauri::command]
fn set_login_item(enabled: bool) -> Result<bool, String> {
    let p = login_plist();
    if !enabled {
        let _ = std::fs::remove_file(&p);
        return Ok(false);
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let x = exe.display().to_string().replace('&', "&amp;").replace('<', "&lt;");
    let plist = format!(r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.mnemos.desktop</string>
<key>ProgramArguments</key><array><string>{x}</string><string>--background</string></array>
<key>RunAtLoad</key><true/>
<key>ProcessType</key><string>Interactive</string>
</dict></plist>
"#);
    if let Some(d) = p.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    std::fs::write(&p, plist).map_err(|e| e.to_string())?;
    Ok(true)
}

#[tauri::command]
fn login_item() -> bool {
    login_plist().exists()
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

/// Something is listening on our port (Mnemos or another app).
fn port_taken() -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], PORT).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// A Mnemos answers on our port (its /health says mode "local"), not just any app that took 8765.
/// The server only answers its own Host (with the port) and the Bearer token it was started with.
fn health_request(tok: &str) -> String {
    format!("GET /health HTTP/1.0\r\nHost: 127.0.0.1:{PORT}\r\nAuthorization: Bearer {tok}\r\n\r\n")
}

fn healthy() -> bool {
    use std::io::Read;
    let addr: SocketAddr = ([127, 0, 0, 1], PORT).into();
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else { return false };
    let _ = s.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = s.set_write_timeout(Some(Duration::from_millis(300)));
    if s.write_all(health_request(&token().unwrap_or_default()).as_bytes()).is_err() {
        return false;
    }
    let mut buf = Vec::new();
    let _ = s.take(4096).read_to_end(&mut buf);
    let body = String::from_utf8_lossy(&buf);
    body.contains("\"mode\":\"local\"") || body.contains("\"mode\": \"local\"")
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
        return Err("Another Mnemos is already running. Quit it from its menu bar icon (Quit Mnemos), then press Start.".into());
    }
    if port_taken() {
        return Err(format!("Port {PORT} is used by another app, not Mnemos (OpenMemory uses it too, for example). \
Quit that app, then press Start."));
    }
    let h = home();
    let log = std::fs::File::create(h.join("hub.log")).map_err(|e| e.to_string())?;
    let tok = token()?;
    let child = hub_command(&["serve", "--home", &h.display().to_string(), "--port", &PORT.to_string()])?
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .env("MNEMOS_TOKEN", tok)
        .env("MNEMOS_PARENT_PID", std::process::id().to_string())
        .spawn()
        .map_err(|e| format!("could not start Mnemos: {e}"))?;
    *g = Some(child);
    Ok(())
}

/// The local app with a one-time unlock: the token travels in the URL fragment (never sent to a server log)
/// and the page swaps it for a session cookie.
#[tauri::command]
fn memory_url() -> Result<String, String> {
    Ok(format!("http://127.0.0.1:{PORT}/app#k={}", token()?))
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
        return Err("spaces: lowercase letters, digits and dashes (e.g. personal, work)".into());
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
    let _ = token(); // create the 0600 token file if init did not
    let _ = set_login_item(true); // a memory that is off is no memory: start with the Mac (toggle in the app)
    Ok(h)
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// Headless entry points (same code paths as the buttons; used by the end-to-end "friend test" and scripts):
///   Mnemos --setup <contexts> <name>   (password on stdin)   = the first-run wizard
///   Mnemos --connect-claude                                 = the Connect button (one entry, every space)
///   Mnemos --open-memory                                    = menu "Open my memory" (browser, token from ~/Mnemos/token)
///   Mnemos --app-cards                                      = the Connect apps screen as JSON (commands, links)
fn headless() -> Option<i32> {
    let args: Vec<String> = std::env::args().collect();
    let flag = args.get(1)?.as_str();
    if flag != "--setup" && flag != "--connect-claude" && flag != "--open-memory" && flag != "--app-cards" {
        return None;
    }
    let py = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join("../Resources/python/bin/python3")))
        .filter(|p| p.is_file()).and_then(|p| p.canonicalize().ok());
    let _ = BUNDLED_PY.set(py);
    let res = if flag == "--app-cards" {
        app_cards().map(|c| serde_json::json!({"apps": c, "cursor_link": cursor_link().unwrap_or_default()}).to_string())
    } else if flag == "--open-memory" {
        memory_url().and_then(|u| Command::new("open").arg(u).status().map(|_| "opened".to_string()).map_err(|e| e.to_string()))
    } else if flag == "--setup" {
        let mut pw = String::new();
        let _ = std::io::stdin().read_line(&mut pw);
        let ctxs = args.get(2).cloned().unwrap_or_else(|| "personal,work".into());
        setup(ctxs.split(',').map(String::from).collect(), args.get(3).cloned().unwrap_or_default(),
              pw.trim_end_matches(['\r', '\n']).to_string())
    } else {
        connect_claude()
    };
    Some(match res {
        Ok(m) => { println!("{m}"); 0 }
        Err(e) => { eprintln!("{e}"); 1 }
    })
}

fn main() {
    if let Some(code) = headless() {
        std::process::exit(code);
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Hub(Mutex::new(None)))
        .setup(|app| {
            let py = app.path().resource_dir().ok().map(|r| r.join("python/bin/python3")).filter(|p| p.is_file());
            let _ = BUNDLED_PY.set(py);
            // always start the hub when set up (MNEMOS_AUTOSTART=0 to skip); --background = menu bar only
            if std::env::var("MNEMOS_DEMO").as_deref() == Ok("1") && !home().join("contexts.yaml").exists() {
                let h = home().display().to_string(); // seed the fictional demo memory once
                if let Ok(mut c) = hub_command(&["demo", "--home", &h]) { let _ = c.output(); }
            }
            if std::env::var("MNEMOS_AUTOSTART").as_deref() != Ok("0") && home().join("contexts.yaml").exists() {
                if let Err(e) = start_hub(app.state::<Hub>()) {
                    eprintln!("autostart failed: {e}");
                }
            }
            if std::env::args().any(|a| a == "--background") {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            let open = MenuItem::with_id(app, "open", "Open my memory", true, None::<&str>)?;
            let show = MenuItem::with_id(app, "show", "Show Mnemos", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Mnemos", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &show, &quit])?;
            let mut tray = TrayIconBuilder::with_id("mnemos").tooltip("Mnemos").menu(&menu);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, ev| match ev.id.as_ref() {
                "open" => {
                    use tauri_plugin_opener::OpenerExt;
                    if let Ok(u) = memory_url() {
                        let _ = app.opener().open_url(u, None::<&str>);
                    }
                }
                "show" => show_main(app),
                "quit" => app.exit(0),
                _ => {}
            })
            .build(app)?;
            Ok(())
        })
        .on_window_event(|w, e| {
            // closing the window keeps Mnemos running in the menu bar; Quit is in the menu
            if let WindowEvent::CloseRequested { api, .. } = e {
                let _ = w.hide();
                api.prevent_close();
            }
        })
        .invoke_handler(tauri::generate_handler![status, start_hub, stop_hub, setup, connect_claude, connect_cursor, app_cards, connect_app, memory_url, claude_notes, open_claude,
                                                 set_login_item, login_item])
        .build(tauri::generate_context!())
        .expect("error while building Mnemos Desktop");
    app.run(|handle, event| {
        #[cfg(target_os = "macos")]
        if let RunEvent::Reopen { .. } = event {
            show_main(handle);
        }
        if let RunEvent::Exit = event {
            // never leave the hub running after quitting the app
            if let Some(mut child) = handle.state::<Hub>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn seen_apps_reads_handshakes() {
        let d = std::env::temp_dir().join(format!("mnemos-seen-{}", std::process::id()));
        std::fs::create_dir_all(d.join("data")).unwrap();
        std::fs::write(d.join("data/apps.json"), r#"{"apps":{"cursor":{"last_seen":"2026-01-01"},"codex":{"last_seen":"x","revoked":true},"vscode":{}}}"#).unwrap();
        std::env::set_var("MNEMOS_HOME", &d);
        assert_eq!(seen_apps(), vec!["cursor".to_string()]);
        std::env::remove_var("MNEMOS_HOME");
        let _ = std::fs::remove_dir_all(d);
    }
    #[test]
    fn health_request_authenticates() {
        let r = health_request("abc");
        assert!(r.contains(&format!("Host: 127.0.0.1:{PORT}\r\n")));
        assert!(r.contains("Authorization: Bearer abc\r\n"));
    }

    #[test]
    fn base64_and_quote() {
        assert_eq!(b64(b"Man"), "TWFu");
        assert_eq!(b64(b"Ma"), "TWE=");
        assert_eq!(b64(b"M"), "TQ==");
        assert_eq!(shell_quote("/Users/ana/Mnemos"), "/Users/ana/Mnemos");
        assert_eq!(shell_quote("/Users/a b/it's"), r"'/Users/a b/it'\''s'");
        assert_eq!(pct("a+b/c="), "a%2Bb%2Fc%3D");
    }
}
