"""Desktop first run (FTUE #189, #190, #199): only detected apps, one-click connect for every local app, live check."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "desktop" / "ui"
RS = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri" / "src" / "main.rs"


def test_connect_flow_is_one_click_with_live_check():
    js = (UI / "app.js").read_text()
    assert 'invoke("connect_app"' in js and "Connected ✓" in js and "c.seen" in js
    assert "Not on this Mac" in js and "No AI app found" in js
    assert "showError" in js and 'id="fix"' in (UI / "index.html").read_text()
    rs = RS.read_text()
    assert "fn connect_app(" in rs and "fn seen_apps(" in rs and "connect_app," in rs


def test_first_run_screen_has_no_jargon():
    html = (UI / "index.html").read_text()
    main = html.split('id="main"')[1]
    for word in ("MCP", "stdio", "token"):
        assert word not in main
    assert "Tailscale" not in main and "Reach from anywhere" not in main


def test_cards_one_state_each_and_cloud_apps_first_class():
    js = (UI / "app.js").read_text()
    for t in ('T("Installed ✓")', 'T("Connected ✓")', 'T("Not connected")', 'cloudCard', '"ChatGPT", "Grok"', "cardMsg[c.id]"):
        assert t in js, t
    assert "Settings → Account → Instructions for Claude" in js and "Profile" not in js


def test_claude_text_has_no_tool_names_and_extension_is_switched_off_with_backup():
    rs = (ROOT / "desktop/src-tauri/src/main.rs").read_text()
    prefs = rs.split("const CLAUDE_PREFS: &str = ")[1].split(";\n")[0]
    assert "memory_" not in prefs and "Mnemos" in prefs
    dis = rs.split("fn disable_claude_extension")[1].split("\n}\n")[0]
    assert "mnemos-backup" in dis and '"isEnabled"' in dis and "remove_" not in dis
    assert "fn claude_code_direct" in rs and "fn broken_bin" in rs
