"""Files Memory screen API (0.11): list, read, edit with a version check, history, restore."""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from hub_gateway import local

W = {"X-Mnemos-Write": "1"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "files")
    monkeypatch.setenv("MNEMOS_HYGIENE", "0")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal"], "Ana")

    async def seed():
        servers = local.build_all(h, 8765)
        async with Client(servers["personal"]) as a:
            await a.call_tool("memory_remember", {"text": "Ana prefers short answers", "tags": ["kind-preference"]})
    asyncio.run(seed())
    return h


def test_files_endpoints(home):
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        assert c.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W).status_code == 200
        assert c.get("/api/session").json()["store"] == "files"
        spaces = c.get("/api/files", params={"context": "personal"}).json()["spaces"]
        rel = "personal/profile.md"
        assert any(t["file"] == rel for s in spaces for t in s["topics"])
        f = c.get("/api/files", params={"context": "personal", "op": "read", "file": rel}).json()
        assert "prefers short" in f["text"].lower()
        w = c.post("/api/files", json={"context": "personal", "op": "write", "file": rel,
                                       "text": f["text"] + "- Ana drinks mate\n", "version": f["version"]}, headers=W).json()
        assert not w.get("error"), w
        stale = c.post("/api/files", json={"context": "personal", "op": "write", "file": rel, "text": "x",
                                           "version": f["version"]}, headers=W).json()
        assert "cambió" in stale["error"]  # version check: someone else saved first
        hist = c.get("/api/files", params={"context": "personal", "op": "history", "file": rel}).json()["history"]
        assert hist[0]["author"] == "you" and len(hist) >= 2
        c.post("/api/files", json={"context": "personal", "op": "restore", "file": rel, "sha": hist[1]["sha"]}, headers=W)
        assert "mate" not in c.get("/api/files", params={"context": "personal", "op": "read", "file": rel}).json()["text"]
        assert c.post("/api/files", json={"context": "personal", "op": "read", "file": "../token"},
                      headers=W).json()["error"]
        st = c.get("/api/memory-migrate").json()
        assert st["active"] is True


def test_graph_works_on_files_store(home):
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        assert c.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W).status_code == 200
        g = c.get("/api/graph", params={"context": "personal"})
        assert g.status_code == 200, g.text
        assert "nodes" in g.json() and "edges" in g.json()
