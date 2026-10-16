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
        who = data(await p.call_tool("hub_whoami", {}))
        assert who["login"] == "local-owner"
