"""Auto-discover import: finds the known sources in a fake home, reads them read-only, drops secrets, previews
with dedupe and context routing, commits only what was approved, and undoes a whole import."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from hub_gateway import local, local_discover as ld
from hub_gateway.local_ui import UI

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def fake_home(h: Path) -> Path:
    """Every source kind, plus traps: secrets, .env, node_modules, an unrelated zip."""
    proj = h / ".claude" / "projects" / "-Users-ana-code-atlas"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "I work at Acme as a data engineer. Fix the build please"}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "My daughter Lucía starts school in March."}]}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "I am a meta line and must be ignored"}},
        {"type": "user", "message": {"role": "user", "content": f"I have the key api_key={SECRET} for the staging box"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "I live in the cloud (assistant, ignored)"}},
    ]
    _write(proj / "s1.jsonl", "\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    _write(h / ".claude" / "CLAUDE.md", "# Preferences\nAlways answer in Spanish and keep it short.\n")
    _write(h / "code" / "atlas" / "AGENTS.md", "# Atlas\nAtlas uses Postgres 16 and deploys to Hetzner.\n")
    _write(h / "code" / "atlas" / ".cursor" / "rules" / "style.mdc", "Use TypeScript strict mode in every package.\n")
    _write(h / "code" / "atlas" / ".env", f"OPENAI_API_KEY={SECRET}\n")
    _write(h / "code" / "atlas" / "node_modules" / "pkg" / "CLAUDE.md", "vendored, never read")
    cur = h / "Library" / "Application Support" / "Cursor" / "User"
    (cur / "globalStorage").mkdir(parents=True)
    c = sqlite3.connect(cur / "globalStorage" / "state.vscdb")
    c.execute("CREATE TABLE cursorDiskKV (key TEXT, value BLOB)")
    c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
    c.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:a:1", json.dumps({"type": 1, "text": "I prefer pnpm over npm for every new repo."})))
    c.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:a:2", json.dumps({"type": 2, "text": "I am the assistant"})))
    c.commit(); c.close()
    (cur / "workspaceStorage" / "w1").mkdir(parents=True)
    c = sqlite3.connect(cur / "workspaceStorage" / "w1" / "state.vscdb")
    c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
    c.execute("INSERT INTO ItemTable VALUES (?,?)", ("aiService.prompts", json.dumps([{"text": "I usually deploy on Fridays after lunch."}])))
    c.commit(); c.close()
    vault = h / "Notes" / "Vault"
    _write(vault / "Family.md", "# Family\nMy brother Tomás lives in Córdoba.\n")
    _write(vault / ".obsidian" / "app.json", "{}")
    _write(h / "Library" / "Application Support" / "obsidian" / "obsidian.json", json.dumps({"vaults": {"x": {"path": str(vault)}}}))
    dl = h / "Downloads"
    dl.mkdir()
    gpt = [{"title": "Trip", "update_time": 1, "mapping": {"1": {"message": {"author": {"role": "user"}, "content": {"parts": ["I love hiking in the Alps every summer."]}}}}}]
    with zipfile.ZipFile(dl / "chatgpt-export.zip", "w") as z:
        z.writestr("conversations.json", json.dumps(gpt))
    cl = [{"name": "x", "chat_messages": [{"sender": "human", "text": "I'm vegetarian since 2019."}, {"sender": "assistant", "text": "ok"}]}]
    with zipfile.ZipFile(dl / "claude-data.zip", "w") as z:
        z.writestr("data/conversations.json", json.dumps(cl))
    with zipfile.ZipFile(dl / "photos.zip", "w") as z:
        z.writestr("a.txt", "nothing")
    return h


def snapshot(root: Path) -> dict[str, tuple[float, str]]:
    return {str(p): (p.stat().st_mtime, hashlib.sha1(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()}


def test_scan_finds_every_kind(tmp_path):
    h = fake_home(tmp_path / "home")
    found = ld.scan(h, ["personal", "work"])
    kinds = {s["kind"]: s for s in found}
    assert set(kinds) == {"claude_code", "claude_md", "agents_md", "cursor_rules", "cursor_chats", "obsidian",
                          "chatgpt_zip", "claude_zip"}
    assert kinds["claude_md"]["count"] == 1  # node_modules skipped
    assert kinds["cursor_chats"]["count"] == 2 and kinds["obsidian"]["count"] == 1
    assert kinds["claude_code"]["context"] == "work" and kinds["obsidian"]["context"] == "personal"
    assert not any(".env" in p for s in found for p in s["paths"])
    assert all("paths" not in s for s in ld.public(found))


def test_secrets():
    assert ld.has_secret(f"token: {SECRET}") and ld.has_secret("postgres://u:pw@db/x")
    assert ld.has_secret("password=hunter2hunter") and not ld.has_secret("I work at Acme as a data engineer.")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.setenv("MNEMOS_DISCOVER_LLM", "0")
    home = tmp_path / "Mnemos"
    local.init_home(home, ["personal", "work"], "Ana")
    s = local.build_all(home, 8765)
    return UI(s, home, "")


async def test_preview_commit_undo_read_only(tmp_path, hub):
    h = fake_home(tmp_path / "home")
    before = snapshot(h)
    d = ld.Discover(hub, h)
    sc = await d.scan({})
    assert len(sc["sources"]) == 8 and sc["llm"] is None
    await d.start({"sources": [{"id": s["id"], "context": s["context"]} for s in sc["sources"]]})
    await d.task
    res = d.job["result"]
    texts = [i["text"] for i in res["items"]]
    joined = "\n".join(texts)
    for want in ("Acme", "Lucía", "Spanish", "Postgres 16", "TypeScript strict", "pnpm", "Fridays", "Tomás",
                 "Alps", "vegetarian"):
        assert want in joined, want
    assert SECRET not in joined and "meta line" not in joined and "assistant" not in joined.lower()
    assert sum(s["secrets_dropped"] for s in res["sources"]) >= 1
    assert snapshot(h) == before  # sources untouched
    # nothing saved yet
    assert not (await hub.call("work", "memory_list", {"limit": 50}))["items"]
    notes = [{"text": i["text"], "context": i["context"], "source": i["source"]} for i in res["items"] if not i["duplicate"]]
    notes.append({"text": f"leak {SECRET} here please", "context": "work", "source": "x"})
    r = await d.commit({"notes": notes})
    assert r["saved"] == len(notes) - 1 and r["skipped"] == 1
    work = (await hub.call("work", "memory_list", {"limit": 100}))["items"]
    personal = (await hub.call("personal", "memory_list", {"limit": 100}))["items"]
    assert any("Acme" in i["text"] for i in work) and any("Tomás" in i["text"] for i in personal)
    assert all(r["batch"] in i["tags"] and "import-auto" in i["tags"] for i in work + personal)
    # a second pass knows them all
    await d.start({"sources": [{"id": s["id"], "context": s["context"]} for s in sc["sources"]]})
    await d.task
    assert d.job["result"]["new"] == 0
    # undo the whole import
    u = await d.undo({"batch": r["batch"]})
    assert u["removed"] == len(notes) - 1
    assert not (await hub.call("work", "memory_list", {"limit": 100}))["items"]


async def test_only_scanned_sources_can_be_read(hub, tmp_path):
    d = ld.Discover(hub, fake_home(tmp_path / "home"))
    with pytest.raises(ValueError):
        await d.start({"sources": [{"id": "deadbeef", "context": "work"}]})
    with pytest.raises(ValueError):
        await d.undo({"batch": "../../etc"})


class FakeLLM:
    kind, name = "builtin", "Built-in · fake"

    def __init__(self):
        self.prompts = []

    async def json(self, prompt):
        self.prompts.append(prompt)
        return {"facts": ["Ana is a data engineer at Acme.", f"Ana's key is {SECRET}", "short"]}


class FakeEmbedder:
    kind, name, model = "builtin", "Built-in · fake-embed", "fake"

    async def embed(self, texts):  # "same meaning" = same first word
        return [[1.0, 0.0] if t.split()[0] in ("Ana", "Ana's") else [0.0, 1.0] for t in texts]


async def test_llm_path_and_semantic_dedupe(tmp_path):
    h = fake_home(tmp_path / "home")
    src = {"id": "x", "kind": "claude_code", "label": "L", "context": "work",
           "paths": [str(p) for p in (h / ".claude" / "projects").rglob("*.jsonl")]}
    llm = FakeLLM()
    res = await ld.preview([src], {}, lambda p: None, llm=llm)
    assert [i["text"] for i in res["items"]] == ["Ana is a data engineer at Acme."]
    assert res["engine"] == {"kind": "builtin", "name": "Built-in · fake"}
    assert llm.prompts and all(SECRET not in p for p in llm.prompts)  # secrets never reach a model
    res = await ld.preview([src], {"work": ["Ana works as a data engineer for Acme Corp"]}, lambda p: None, llm=llm,
                           embedder=FakeEmbedder())
    assert res["semantic_duplicates"] == 1 and res["new"] == 0
