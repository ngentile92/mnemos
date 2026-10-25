"""Desktop first run (FTUE #189, #190, #199): only detected apps, one-click connect for every local app, live check."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "desktop" / "ui"
RS = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri" / "src" / "main.rs"


def test_connect_commands_stay_for_the_headless_checks():
    # v0.18: the window has no Connect buttons (the web app connects); the Rust commands stay for --connect-claude
    rs = RS.read_text()
    assert "fn connect_app(" in rs and "fn seen_apps(" in rs and "connect_app," in rs
    assert 'id="fix"' in (UI / "index.html").read_text()


def test_first_run_screen_has_no_jargon():
    html = (UI / "index.html").read_text()
    main = html.split('id="main"')[1]
    for word in ("MCP", "stdio", "token"):
        assert word not in main
    assert "Tailscale" not in main and "Reach from anywhere" not in main


def test_claude_text_has_no_tool_names_and_extension_is_switched_off_with_backup():
    rs = (ROOT / "desktop/src-tauri/src/main.rs").read_text()
    prefs = rs.split("const CLAUDE_PREFS: &str = ")[1].split(";\n")[0]
    assert "memory_" not in prefs and "Mnemos" in prefs
    dis = rs.split("fn disable_claude_extension")[1].split("\n}\n")[0]
    assert "mnemos-backup" in dis and '"isEnabled"' in dis and "remove_" not in dis
    assert "fn claude_code_direct" in rs and "fn broken_bin" in rs
