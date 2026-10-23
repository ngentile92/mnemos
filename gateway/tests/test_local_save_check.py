"""Local mode: memory_save refuses near-duplicates and points at related notes."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from hub_gateway import local


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


async def test_duplicate_and_related(home):
    s = local.build_all(home, 8765)
    async with Client(s["personal"]) as p:
        r = data(await p.call_tool("memory_save", {"text": "Ana vive en Madrid desde 2020 con su perro Toby"}))
        assert r["status"] != "duplicate"
        r = data(await p.call_tool("memory_save", {"text": "Ana vive en Madrid desde 2020, con su perro Toby."}))
        assert r["status"] == "duplicate" and r["duplicate_of"]["id"]
        assert len(data(await p.call_tool("memory_list", {}))["items"]) == 1
        r = data(await p.call_tool("memory_save", {"text": "Ana vive en Madrid desde 2020, con su perro Toby.",
                                                   "allow_duplicate": True}))
        assert r["status"] != "duplicate"
        r = data(await p.call_tool("memory_save", {"text": "Ana ahora vive en Lisboa con su perro Toby"}))
        assert r["saved_to"] and r["similar_notes"] and r["similar_notes"][0]["kind"] == "related"
        r = data(await p.call_tool("memory_save", {"text": "Ana vive en Madrid desde 2021 con su perro Toby"}))
        assert r["saved_to"] and r["similar_notes"]  # different year: related, not a duplicate
        who = data(await p.call_tool("hub_whoami", {}))
        assert who["login"] == "local-owner"


@pytest.mark.parametrize("store", ["notes", "files"])
async def test_contradiction_across_languages_without_model(tmp_path, monkeypatch, store):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.setenv("MNEMOS_STORE", store)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    s = local.build_all(h, 8765)
    async with Client(s["personal"]) as p:
        tool = "memory_remember" if store == "files" else "memory_save"
        await p.call_tool(tool, {"text": "Prefers dark mode in every app"})
        r = data(await p.call_tool(tool, {"text": "No me gusta el modo oscuro"}))
        assert any(n["kind"] == "contradicts" for n in r.get("similar_notes") or []), r
        assert "contradict" in r["note"]
