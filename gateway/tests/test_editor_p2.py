"""Memory editor phase 2: pin, mark obsolete, 'esto no es así' proposals (propose-only). Mock Cognee/LLM."""
from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hub_gateway.answer import Answerer
from hub_gateway.app import build_server
from hub_gateway.audit import Audit
from hub_gateway.config import Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS
from test_server import NOTE_IDS, CogneeRecorder, data, dev_env, make_server  # noqa: F401

PID = NOTE_IDS["personal"]


async def search(c, q="orion"):
    return data(await c.call_tool("memory_search", {"query": q, "mode": "keyword"}))["results"]


async def test_pin_and_obsolete_shape_search(make_server):  # noqa: F811
    server, rec = make_server("personal")
    async with Client(server) as c:
        assert any(r.get("id") == PID for r in await search(c))
        await c.call_tool("memory_pin", {"id": PID})
        hit = next(r for r in await search(c) if r.get("id") == PID)
        assert hit["pinned"] is True
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        assert items[PID]["pinned"] is True and items[PID]["obsolete"] is False
        await c.call_tool("memory_mark_obsolete", {"id": PID, "reason": "cambió"})
        assert not any(r.get("id") == PID for r in await search(c))
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        assert items[PID]["obsolete"] is True and items[PID]["provenance"]["obsolete"]["reason"] == "cambió"
        await c.call_tool("memory_mark_obsolete", {"id": PID, "obsolete": False})
        assert any(r.get("id") == PID for r in await search(c))
        assert rec.texts.get(PID, "nota de personal sobre orion") == "nota de personal sobre orion"  # text untouched


async def test_flags_refused_on_shared(make_server):  # noqa: F811
    server, _ = make_server("personal")
    async with Client(server) as c:
        with pytest.raises(ToolError):
            await c.call_tool("memory_pin", {"id": NOTE_IDS["shared"]})
        with pytest.raises(ToolError):
            await c.call_tool("memory_mark_obsolete", {"id": NOTE_IDS["work"]})


def llm(reply):
    def h(req):
        body = json.loads(req.content)
        assert "CORRECTION" in body["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
    return Answerer("http://ollama", "m", transport=httpx.MockTransport(h))


def server_with(monkeypatch, tmp_path, skills_repo, policy_file, answerer):
    dev_env(monkeypatch, tmp_path, "personal")
    rec = CogneeRecorder()
    return build_server(
        Settings.from_env(),
        cognee=CogneeClient("http://cognee:8000", "k", transport=httpx.MockTransport(lambda r: rec.handler(r))),
        datasets=DatasetMap(DATASETS), skills=SkillIndex(skills_repo, "personal"),
        broker=SecretBroker(load_policy(str(policy_file), "personal"), None),
        audit=Audit("personal", str(tmp_path / "audit.log")), answerer=answerer), rec


async def test_dispute_proposes_without_writing(monkeypatch, tmp_path, skills_repo, policy_file):
    server, rec = server_with(monkeypatch, tmp_path, skills_repo, policy_file, llm(
        {"related": True, "action": "update", "proposed_text": "nota de personal sobre orion (ya cerrado)", "why": "x"}))
    async with Client(server) as c:
        out = data(await c.call_tool("memory_dispute", {"correction": "orion ya está cerrado"}))
    ids = [p["id"] for p in out["proposals"]]
    assert PID in ids and NOTE_IDS["shared"] not in ids and NOTE_IDS["work"] not in ids
    p = next(p for p in out["proposals"] if p["id"] == PID)
    assert p["action"] == "update" and "cerrado" in p["proposed_text"]
    assert not rec.patches and not rec.deletes  # propose only


async def test_dispute_drops_unrelated(monkeypatch, tmp_path, skills_repo, policy_file):
    server, _ = server_with(monkeypatch, tmp_path, skills_repo, policy_file, llm({"related": False, "action": "update"}))
    async with Client(server) as c:
        out = data(await c.call_tool("memory_dispute", {"correction": "orion ya está cerrado"}))
    assert out["proposals"] == []
