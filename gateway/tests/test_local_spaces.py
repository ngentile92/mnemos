"""Spaces screen: label/colour/description and read-only bridges, written to contexts.yaml and live-reloaded."""
from __future__ import annotations

import json

import pytest
import yaml
from fastmcp import Client
from mcp.types import Implementation

from hub_gateway import contexts, local
from hub_gateway import local_spaces as lsp


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    import os
    from pathlib import Path
    monkeypatch.setenv("HUB_CONTEXTS_FILE", os.environ["HUB_CONTEXTS_FILE"])  # local._env changes it
    yield h
    contexts.reload(Path(__file__).resolve().parents[2] / "config" / "contexts.example.yaml")  # what other tests use


def test_read_update_and_validation(home):
    r = lsp.read(home)
    assert [s["name"] for s in r["spaces"]] == ["personal", "work"]
    assert r["spaces"][0]["label"] == "Personal" and r["spaces"][0]["color"].startswith("#") and "Shared" in r["shared"]
    r = lsp.update_space(home, "work", label="Trabajo", color="#22D3EE", description="my job at Acme: clients, team")
    w = next(s for s in r["spaces"] if s["name"] == "work")
    assert w == {"name": "work", "label": "Trabajo", "color": "#22d3ee", "description": "my job at Acme: clients, team"}
    assert (home / "data" / "contexts.yaml.bak").exists()
    raw = yaml.safe_load((home / "contexts.yaml").read_text())
    assert raw["owner"] == "Ana" and raw["contexts"]["work"]["label"] == "Trabajo"
    for bad in ({"color": "red"}, {"label": ""}, {"description": "x"}):
        with pytest.raises(ValueError):
            lsp.update_space(home, "work", **bad)
    with pytest.raises(ValueError):
        lsp.update_space(home, "nope", label="x")
    with pytest.raises(ValueError):
        lsp.set_bridge(home, "work", "work", True)


async def test_bridge_toggle_reloads_router_in_place(home):
    local._env(home)
    contexts.reload(home / "contexts.yaml")
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers)
    ci = Implementation(name="cursor-vscode", version="1")
    async with Client(router, client_info=ci) as c:
        await c.call_tool("memory_save", {"text": "Ana lanza el proyecto Mnemos en noviembre", "context": "personal"})
        hits = data(await c.call_tool("memory_search", {"query": "Mnemos noviembre", "mode": "keyword", "context": "work",
                                                        "include_sources": False}))["results"]
        assert not hits  # isolated
    lsp.set_bridge(home, "personal", "work", True)
    lsp.update_space(home, "work", description="my job at Acme")
    async with Client(router, client_info=ci) as c:  # next call notices the edited file (stdio processes too)
        hits = data(await c.call_tool("memory_search", {"query": "Mnemos noviembre", "mode": "keyword", "context": "work",
                                                        "include_sources": False}))["results"]
        assert any("noviembre" in h["text"] for h in hits)
        who = data(await c.call_tool("hub_whoami", {}))
        assert next(s for s in who["spaces"] if s["name"] == "work")["description"] == "my job at Acme"
    assert "my job at Acme" in router.instructions
    lsp.set_bridge(home, "personal", "work", False)
    async with Client(router, client_info=ci) as c:
        hits = data(await c.call_tool("memory_search", {"query": "Mnemos noviembre", "mode": "keyword", "context": "work",
                                                        "include_sources": False}))["results"]
        assert not hits
    assert "bridges" not in yaml.safe_load((home / "contexts.yaml").read_text())


def test_spaces_api(home):
    from starlette.testclient import TestClient
    W = {"X-Mnemos-Write": "1"}
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/spaces").status_code == 401
        c.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W)
        assert [s["name"] for s in c.get("/api/spaces").json()["spaces"]] == ["personal", "work"]
        assert c.post("/api/spaces/update", json={"name": "work", "label": "Job"}).status_code == 403  # no header
        r = c.post("/api/spaces/update", json={"name": "work", "label": "Job", "description": "my job"}, headers=W)
        assert r.status_code == 200 and r.json()["spaces"][1]["label"] == "Job"
        assert c.post("/api/spaces/update", json={"name": "work", "color": "blue"}, headers=W).status_code == 400
        r = c.post("/api/spaces/bridge", json={"from": "personal", "to": "work", "on": True}, headers=W)
        assert r.json()["bridges"] == [{"from": "personal", "to": "work"}]
        assert contexts.BRIDGES and contexts.BRIDGES[0].reader == "work"  # live, no restart
