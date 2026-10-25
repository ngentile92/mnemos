"""v0.18 B: connect every AI from the web app; detection only looks at whether apps and config files exist."""
import json
from pathlib import Path

import pytest

from hub_gateway import local_connect as C

ROOT = Path(__file__).resolve().parents[2]
ST = ROOT / "gateway/src/hub_gateway/local_ui_static"


@pytest.fixture
def h(tmp_path, monkeypatch):
    u = tmp_path / "user"
    u.mkdir()
    monkeypatch.setenv("MNEMOS_SCAN_HOME", str(u))
    monkeypatch.setenv("MNEMOS_CLAUDE_CONFIG", str(u / "Claude/claude_desktop_config.json"))
    monkeypatch.delenv("MNEMOS_HUB_CMD", raising=False)
    return u


def test_nothing_found_on_an_empty_mac(h):
    d = C.detect()
    assert not any(v["configured"] for v in d.values())
    assert not d["claude-code"]["installed"] or C.shutil.which("claude")


def test_claude_desktop_keeps_other_servers_and_backs_up(h, tmp_path):
    p = h / "Claude/claude_desktop_config.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}, "mnemos-work": {"command": "py", "args": ["stdio"]}}, "theme": 1}))
    r = C.connect("claude-desktop", tmp_path / "Mnemos")
    cfg = json.loads(p.read_text())
    assert r["ok"] and "⌘Q" in r["next"]
    assert set(cfg["mcpServers"]) == {"other", "mnemos"} and cfg["theme"] == 1
    assert cfg["mcpServers"]["mnemos"]["args"][-3:] == ["stdio", "--home", str(tmp_path / "Mnemos")]
    assert list(p.parent.glob("*.mnemos-backup-*"))
    assert C.detect()["claude-desktop"]["configured"]


def test_invalid_config_is_never_touched(h, tmp_path):
    p = h / ".claude.json"
    p.write_text("{not json")
    with pytest.raises(ValueError):
        C.connect("claude-code", tmp_path / "M")
    assert p.read_text() == "{not json"


def test_codex_block_is_shared_with_chatgpt_desktop(h, tmp_path):
    (h / ".codex").mkdir()
    (h / ".codex/config.toml").write_text('model = "x"\n')
    r = C.connect("chatgpt", tmp_path / "M")
    t = (h / ".codex/config.toml").read_text()
    assert t.startswith('model = "x"\n') and t.count("[mcp_servers.mnemos]") == 1 and "Codex or Work" in r["next"]
    C.connect("codex", tmp_path / "M")  # idempotent
    assert (h / ".codex/config.toml").read_text().count("[mcp_servers.mnemos]") == 1
    import tomllib
    assert tomllib.loads((h / ".codex/config.toml").read_text())["mcp_servers"]["mnemos"]["args"][-1] == str(tmp_path / "M")
    d = C.detect()
    assert d["codex"]["configured"] and not d["chatgpt"]["configured"]  # no ChatGPT app on this test Mac


def test_cursor_uses_its_install_link_and_cloud_apps_refuse(h, tmp_path):
    r = C.connect("cursor", tmp_path / "M")
    assert r["open"].startswith("cursor://anysphere.cursor-deeplink/mcp/install?name=mnemos&config=")
    for k in ("grok", "claude-web"):
        with pytest.raises(ValueError):
            C.connect(k, tmp_path / "M")


def test_connected_only_after_a_real_first_call(h, tmp_path):
    (h / ".codex").mkdir()
    C.connect("codex", tmp_path / "M")
    c = {a["key"]: a for a in C.cards(tmp_path / "M", {})["ais"]}
    assert c["codex"]["configured"] and not c["codex"]["seen"]
    c = {a["key"]: a for a in C.cards(tmp_path / "M", {"codex": {"last_seen": "2026-10-25T10:00:00"}})["ais"]}
    assert c["codex"]["seen"] and not c["grok"]["seen"]
    c = {a["key"]: a for a in C.cards(tmp_path / "M", {"claude-desktop": {"last_seen": "x", "via": "remote"}})["ais"]}
    assert c["claude-web"]["seen"]


def test_detection_never_reads_private_app_data():
    src = (ROOT / "gateway/src/hub_gateway/local_connect.py").read_text()
    for private in ("IndexedDB", "Local Storage", "Cache", "conversations", "Application Support/com.openai"):
        assert private not in src


def test_web_app_connects_and_the_window_does_not():
    js = (ST / "connect.js").read_text()
    assert '"/api/connect", { key }' in js and "data-connect" in js and "Not on this Mac:" in js
    desk = (ROOT / "desktop/ui/app.js").read_text()
    assert "connect_app" not in desk and "app_cards" not in desk
    assert "Open my memory → AIs" in (ROOT / "desktop/ui/index.html").read_text()
