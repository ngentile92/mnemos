"""Migration: notes store (SQLite) ↔ simple memory (Markdown files). Never touches a real home."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from hub_gateway import local, memfiles, memmigrate


@pytest.fixture
def notes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "notes")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


async def _seed(home):
    servers = local.build_all(home, 8765)
    async with Client(servers["personal"]) as a:
        await a.call_tool("memory_save", {"text": "Ana prefers short answers in Spanish", "tags": ["kind-preference"]})
        await a.call_tool("memory_save", {"text": "Ana's sister Laura lives in Rosario", "tags": ["kind-person"]})
        await a.call_tool("memory_save", {"text": "Ana is building Mnemos", "tags": ["kind-project_state"]})
        r = await a.call_tool("memory_list", {})
        items = (r.structured_content or json.loads(r.content[0].text))["items"]
        stale = next(i for i in items if "Laura" in i["text"])
        await a.call_tool("memory_mark_obsolete", {"id": stale["id"], "reason": "moved"})
    return servers


def test_preview_writes_nothing_to_memory(notes_home):
    import asyncio
    asyncio.run(_seed(notes_home))
    rep = memmigrate.preview(notes_home)
    assert rep["notes"] >= 3 and not memfiles.active(notes_home)
    assert (notes_home / "migration-preview" / "report.md").exists()
    assert (notes_home / "migration-preview" / "memory" / "personal" / "profile.md").exists()
    assert not (notes_home / "memory").exists()
    text = (notes_home / "migration-preview" / "memory" / "personal" / "topics" / "people.md").read_text()
    assert "Laura" in text and "## No longer true" in text


def test_apply_keeps_ids_and_undo_goes_back(notes_home):
    import asyncio
    asyncio.run(_seed(notes_home))
    before = memmigrate.read_notes(notes_home)
    ids_before = {n["id"] for ns in before.values() for n in ns}
    applied = memmigrate.apply(notes_home)
    assert applied["status"] == "applied" and memfiles.active(notes_home)
    store = memfiles.FileNoteStore(notes_home / "memory", None,
                                   {__import__("hub_gateway.localstore", fromlist=["dataset_id"]).dataset_id(n): n
                                    for n in before})
    found = {b["id"] for b in store.repo.scan("personal")}
    assert ids_before & found == ids_before  # every note id survived
    # a note saved AFTER apply must come back into SQLite on undo
    import asyncio as _a
    async def add():
        servers = local.build_all(notes_home, 8765)
        async with Client(servers["personal"]) as a:
            r = await a.call_tool("memory_remember", {"text": "Ana started yoga on Mondays"})
            return (r.structured_content or json.loads(r.content[0].text))["id"]
    new_id = _a.run(add())
    undone = memmigrate.undo(notes_home)
    assert undone["status"] == "undone" and not memfiles.active(notes_home)
    assert undone["copied_back"]["added"] >= 1
    after = memmigrate.read_notes(notes_home)
    assert any(n["id"] == new_id for ns in after.values() for n in ns)
