"""0.12 native memory for coding agents: opt-in, own file/block only, backups, clean removal."""
from __future__ import annotations

from hub_gateway import local, local_sync


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "files")
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("MNEMOS_SCAN_HOME", str(user))
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    (h / "memory" / "work").mkdir(parents=True, exist_ok=True)
    (h / "memory" / "work" / "profile.md").write_text("---\ntitle: About me\n---\n# About me\n\n- Ana prefers pnpm\n")
    (h / "memory" / "personal").mkdir(parents=True, exist_ok=True)
    (h / "memory" / "personal" / "profile.md").write_text("# About me\n\n- Ana has two cats\n")
    return h, user


def test_off_by_default(tmp_path, monkeypatch):
    h, user = _home(tmp_path, monkeypatch)
    assert local_sync.refresh(h) == []
    assert not (user / ".claude").exists()
    assert all(not t["on"] for t in local_sync.status(h, ["personal", "work"])["targets"])


def test_claude_code_file_only_picked_spaces_and_removal(tmp_path, monkeypatch):
    h, user = _home(tmp_path, monkeypatch)
    local_sync.set_target(h, "claude-code", True, ["work"], ["personal", "work"])
    f = user / ".claude" / "rules" / "mnemos.md"
    t = f.read_text()
    assert "pnpm" in t and "cats" not in t  # only the space the user picked
    local_sync.set_target(h, "claude-code", False, None, ["personal", "work"])
    assert not f.exists()


def test_codex_block_keeps_the_users_text_and_backs_up(tmp_path, monkeypatch):
    h, user = _home(tmp_path, monkeypatch)
    agents = user / ".codex" / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("# My rules\n\nAlways run tests.\n")
    r = local_sync.set_target(h, "codex", True, ["personal"], ["personal", "work"])
    assert r["backup"] and "Always run tests." in open(r["backup"]).read()
    t = agents.read_text()
    assert t.startswith("# My rules") and "cats" in t and t.count("mnemos:start") == 1
    (h / "memory" / "personal" / "profile.md").write_text("# About me\n\n- Ana has three cats\n")
    local_sync.refresh(h)
    t = agents.read_text()
    assert "three cats" in t and "two cats" not in t and t.count("mnemos:start") == 1
    local_sync.set_target(h, "codex", False, None, ["personal", "work"])
    assert agents.read_text() == "# My rules\n\nAlways run tests.\n"


def test_cursor_plugin_rule(tmp_path, monkeypatch):
    h, user = _home(tmp_path, monkeypatch)
    local_sync.set_target(h, "cursor", True, ["work"], ["personal", "work"])
    rule = user / ".cursor" / "plugins" / "local" / "mnemos" / "rules" / "mnemos.mdc"
    assert rule.read_text().startswith("---\n") and "alwaysApply: true" in rule.read_text()
    assert (user / ".cursor" / "plugins" / "local" / "mnemos" / ".cursor-plugin" / "plugin.json").exists()
    local_sync.set_target(h, "cursor", False, None, ["personal", "work"])
    assert not (user / ".cursor" / "plugins" / "local" / "mnemos").exists()
