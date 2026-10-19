"""Simple memory (0.11): a new home stores memory as Markdown files; assistants see six memory tools."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from hub_gateway import local, memfiles


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "files")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    monkeypatch.delenv("MNEMOS_LABS", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


def client(router, name="claude-ai"):
    return Client(router, client_info=Implementation(name=name, version="1.0"))


def test_new_home_starts_on_files(home, tmp_path, monkeypatch):
    assert memfiles.active(home)
    monkeypatch.setenv("MNEMOS_STORE", "notes")
    old = tmp_path / "Old"
    local.init_home(old, ["personal"], "Ana")
    assert not memfiles.active(old)


async def test_six_tools_and_the_files(home):
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers)
    names = {t.name for t in await router.list_tools()}
    assert {"memory_start", "memory_read", "memory_search", "memory_remember", "memory_edit", "memory_forget",
            "hub_whoami", "hub_use_context", "skills_list", "skills_get"} == names
    async with client(router) as c:
        r = data(await c.call_tool("memory_remember", {"context": "personal", "text": "Ana prefers short answers in Spanish",
                                                       "tags": ["kind-preference"]}))
        assert r["file"] == "personal/profile.md"
        r2 = data(await c.call_tool("memory_remember", {"context": "personal", "text": "Ana's sister Laura lives in Rosario",
                                                        "topic": "people"}))
        assert r2["file"] == "personal/topics/people.md"
        start = data(await c.call_tool("memory_start", {"context": "personal"}))
        assert "short answers" in start["profile"]["personal"]
        assert any(t["topic"] == "people" for t in start["topics"])
        rd = data(await c.call_tool("memory_read", {"context": "personal", "topic": "people"}))
        nid = next(n["id"] for n in rd["notes"] if "Laura" in n["text"])
        found = data(await c.call_tool("memory_search", {"context": "personal", "query": "Laura Rosario", "mode": "keyword"}))
        assert any("Laura" in x["text"] for x in found["results"])
        e = data(await c.call_tool("memory_edit", {"context": "personal", "id": nid, "mode": "replace",
                                                   "text": "Ana's sister Laura lives in Córdoba"}))
        assert e["new_id"] and e["file"] == "personal/topics/people.md"
        text = (home / "memory" / "personal" / "topics" / "people.md").read_text()
        assert "~~Ana's sister Laura lives in Rosario~~" in text and "## No longer true" in text
        assert "- Ana's sister Laura lives in Córdoba" in text
        await c.call_tool("memory_forget", {"context": "personal", "id": e["new_id"]})
        assert "Córdoba" not in (home / "memory" / "personal" / "topics" / "people.md").read_text()
        with pytest.raises(ToolError, match="secreto"):
            await c.call_tool("memory_remember", {"context": "personal", "text": "my github token is ghp_" + "a" * 36})
    repo = memfiles.MemoryRepo(home / "memory")
    log = repo.history("personal", 20)
    assert any(h["author"].startswith("Claude") for h in log)  # commits carry the app that made the change
    assert len(log) >= 4


async def test_legacy_tools_still_work_in_process_and_labs_brings_entity(home):
    from hub_gateway import local_labs
    servers = local.build_all(home, 8765)
    async with Client(servers["personal"]) as a:
        await a.call_tool("memory_save", {"text": "Ana runs every morning at 7"})
        items = data(await a.call_tool("memory_list", {}))["items"]
    assert any("runs every morning" in i["text"] for i in items)
    local_labs.set_enabled(home, True)
    router = local.build_router(home, servers)
    assert "memory_entity" in {t.name for t in await router.list_tools()}
