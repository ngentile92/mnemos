import os

import pytest

from hub_gateway import local, local_native as ln
from hub_gateway.local_ui import UI


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    home = tmp_path / "Mnemos"
    local.init_home(home, ["personal", "work"], "Ana")
    return UI(local.build_all(home, 8765), home, "")


def fake(h):
    (h / ".claude" / "rules").mkdir(parents=True)
    (h / ".claude" / "CLAUDE.md").write_text("# Style\nAnswer briefly.\n\n# Stack\nPython and Postgres.\napi_key = sk-abcdefghijklmnopqrstu\n")
    (h / ".claude" / "rules" / "mnemos.md").write_text("written by mnemos")
    (h / ".claude" / "projects" / "-code-site" / "memory").mkdir(parents=True)
    (h / ".claude" / "projects" / "-code-site" / "memory" / "notes.md").write_text("Site deploys on Fridays.")
    (h / ".codex" / "memories").mkdir(parents=True)
    (h / ".codex" / "AGENTS.md").write_text("Use pnpm.")
    (h / ".codex" / "memories" / "m1.md").write_text("Prefers tabs.")
    (h / ".cursor" / "rules").mkdir(parents=True)
    (h / ".cursor" / "rules" / "style.mdc").write_text("Always TypeScript strict.")
    (h / ".claude" / ".env").write_text("SECRET=1")


def snap(h):
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in h.rglob("*") if p.is_file()}


def test_listing_is_read_only_exact_and_hides_secrets(tmp_path):
    fake(tmp_path)
    before = snap(tmp_path)
    res = ln.listing(tmp_path)
    assert snap(tmp_path) == before
    by = {a["key"]: a for a in res["apps"]}
    cc = {os.path.basename(i["path"]): i for i in by["claude-code"]["items"]}
    assert set(cc) == {"CLAUDE.md", "mnemos.md", "notes.md"} and cc["mnemos.md"]["mnemos"]
    assert "Answer briefly." in cc["CLAUDE.md"]["content"] and "sk-abc" not in cc["CLAUDE.md"]["content"]
    assert [s["title"] for s in cc["CLAUDE.md"]["sections"]] == ["Style", "Stack"]
    assert {os.path.basename(i["path"]) for i in by["codex"]["items"]} == {"AGENTS.md", "m1.md"}
    assert by["cursor"]["items"][0]["content"] == "Always TypeScript strict."
    assert all(".env" not in i["path"] for a in res["apps"] for i in a["items"])


async def test_import_one_section(tmp_path, hub):
    fake(tmp_path / "h")
    n = ln.Native(hub, tmp_path / "h")
    await n.get({})
    p = str(tmp_path / "h" / ".claude" / "CLAUDE.md")
    r = await n.import_one({"path": p, "section": 0, "app": "claude-code", "context": "personal"})
    assert r["saved"] and r["context"] == "personal"
    items = (await hub.call("personal", "memory_list", {"limit": 20}))["items"]
    assert any("Answer briefly" in i["text"] and "import-native" in i["tags"] for i in items)
    with pytest.raises(ValueError):
        await n.import_one({"path": "/etc/passwd", "section": 0})
