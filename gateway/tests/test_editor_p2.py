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
    import re

    def h(req):
        body = json.loads(req.content)
        prompt = body["messages"][0]["content"]
        assert "CORRECTION" in prompt
        m = re.search(r"\[(\d+)\] nota de personal sobre orion", prompt)
        out = {"matches": [{**reply, "n": int(m.group(1))}]} if (m and reply) else {"matches": []}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})
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
        {"action": "update", "evidence": "personal sobre orion", "proposed_text": "nota de personal sobre orion cerrado", "why": "x"}))
    async with Client(server) as c:
        out = data(await c.call_tool("memory_dispute", {"correction": "orion ya está cerrado"}))
    ids = [p["id"] for p in out["proposals"]]
    assert PID in ids and NOTE_IDS["shared"] not in ids and NOTE_IDS["work"] not in ids
    p = next(p for p in out["proposals"] if p["id"] == PID)
    assert p["action"] == "update" and "cerrado" in p["proposed_text"] and p["diff"]
    assert not rec.patches and not rec.deletes  # propose only


async def test_dispute_drops_unrelated(monkeypatch, tmp_path, skills_repo, policy_file):
    server, _ = server_with(monkeypatch, tmp_path, skills_repo, policy_file, llm({}))
    async with Client(server) as c:
        out = data(await c.call_tool("memory_dispute", {"correction": "orion ya está cerrado"}))
    assert out["proposals"] == [] and out["message"]


async def test_background_update_returns_fast_and_overlays(make_server):  # noqa: F811
    import asyncio
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_update", {"id": PID, "text": "texto nuevo en segundo plano", "background": True}))
        assert out["status"] == "processing" and out["version"] == 2
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        assert items[PID]["text"] == "texto nuevo en segundo plano"
        for _ in range(50):
            if rec.patches:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
        assert rec.texts[PID] == "texto nuevo en segundo plano"
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        assert "sync" not in items[PID]
        h = data(await c.call_tool("memory_history", {"id": PID}))
        assert h["previous_versions"][0]["text"] == "nota de personal sobre orion"


def slow_patch(rec, apply: bool):
    """Cognee busy: the PATCH times out on the client; with apply=True Cognee still applies it afterwards."""
    orig = rec.handler

    def handler(request):
        if request.method == "PATCH":
            if apply:
                orig(request)
            raise httpx.ReadTimeout("timed out", request=request)
        return orig(request)
    rec.handler = handler


async def wait_sync_cleared(c, timeout=2.0):
    import asyncio
    for _ in range(int(timeout / 0.02)):
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        if items[PID].get("sync") != "processing":
            return items[PID]
        await asyncio.sleep(0.02)
    return items[PID]


async def test_update_timeout_becomes_processing_then_confirms(make_server, monkeypatch):  # noqa: F811
    from hub_gateway import app as app_mod
    monkeypatch.setattr(app_mod, "UPDATE_POLL_S", 0.01)
    server, rec = make_server("personal")
    slow_patch(rec, apply=True)
    async with Client(server) as c:
        out = data(await c.call_tool("memory_update", {"id": PID, "text": "texto nuevo con Cognee ocupado"}))
        assert out["status"] == "processing"
        item = await wait_sync_cleared(c)
        assert "sync" not in item and item["text"] == "texto nuevo con Cognee ocupado"
        h = data(await c.call_tool("memory_history", {"id": PID}))
        assert h["previous_versions"][0]["text"] == "nota de personal sobre orion"


async def test_update_timeout_never_applied_ends_in_error(make_server, monkeypatch):  # noqa: F811
    from hub_gateway import app as app_mod
    monkeypatch.setattr(app_mod, "UPDATE_POLL_S", 0.01)
    monkeypatch.setattr(app_mod, "UPDATE_CONFIRM_S", 0.05)
    server, rec = make_server("personal")
    slow_patch(rec, apply=False)
    async with Client(server) as c:
        out = data(await c.call_tool("memory_update", {"id": PID, "text": "esto nunca llega a Cognee"}))
        assert out["status"] == "processing"
        item = await wait_sync_cleared(c)
        assert item["sync"] == "error"
    assert rec.texts[PID] == "nota de personal sobre orion"


async def test_history_falls_back_for_old_notes(make_server):  # noqa: F811
    server, _ = make_server("personal")
    async with Client(server) as c:
        h = data(await c.call_tool("memory_history", {"id": PID}))
    assert h["previous_versions"] == [] and "cognee" in h["provenance"]["source"]


async def test_validity_deprioritizes_expired(make_server):  # noqa: F811
    server, rec = make_server("personal")
    async with Client(server) as c:
        await c.call_tool("memory_set_validity", {"id": PID, "valid_from": "2024-01-01", "valid_until": "2025-06-30"})
        hit = next(r for r in await search(c) if r.get("id") == PID)
        assert hit["validity"] == "expired" and hit["valid_until"] == "2025-06-30"
        res = await search(c)
        assert res[-1]["id"] == PID or len(res) == 1  # still returned, but last
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
        assert items[PID]["validity"] == "expired" and items[PID]["provenance"]["valid_from"] == "2024-01-01"
        await c.call_tool("memory_set_validity", {"id": PID, "valid_until": ""})
        hit = next(r for r in await search(c) if r.get("id") == PID)
        assert "validity" not in hit and hit["valid_from"] == "2024-01-01"
        for bad in ({"id": PID, "valid_from": "ayer"}, {"id": PID},
                    {"id": PID, "valid_from": "2026-01-02", "valid_until": "2026-01-01"}):
            with pytest.raises(ToolError):
                await c.call_tool("memory_set_validity", bad)
        assert rec.texts.get(PID, "nota de personal sobre orion") == "nota de personal sobre orion"


def test_validity_state():
    from hub_gateway.ledger import validity_state
    assert validity_state(None, "2026-01-01", "2026-02-01") == "expired"
    assert validity_state("2026-03-01", None, "2026-02-01") == "future"
    assert validity_state("2026-01-01", "2026-12-31", "2026-02-01") is None
