"""Desktop first run (FTUE #189, #190, #199): only detected apps, one-click connect for every local app, live check."""

from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "desktop" / "ui"
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
    assert "Later:" in main
