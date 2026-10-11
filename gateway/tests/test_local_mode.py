"""Single-process local mode (no Docker, no Cognee): tools work end to end and contexts stay isolated."""
from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hub_gateway import local
from hub_gateway.localstore import LocalNoteStore, dataset_id
from hub_gateway.memory import MemoryError_


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    made = local.init_home(h, ["personal", "work"], "Ana")
    assert (h / "token").stat().st_mode & 0o777 == 0o600 and made
    assert local.init_home(h, ["personal", "work"]) == []  # idempotent
    return h


async def test_tools_end_to_end_and_isolation(home):
    s = local.build_all(home, 8765)
    async with Client(s["personal"]) as p, Client(s["work"]) as w:
        await p.call_tool("memory_save", {"text": "Ana prefiere el té verde por la mañana"})
        items = data(await p.call_tool("memory_list", {}))["items"]
        nid = next(i["id"] for i in items if "té verde" in i["text"])
        hits = data(await p.call_tool("memory_search", {"query": "té verde", "mode": "keyword"}))["results"]
        assert any("té verde" in r["text"] for r in hits)
        # the other context sees nothing and cannot touch the note
        assert not any("té verde" in i["text"] for i in data(await w.call_tool("memory_list", {}))["items"])
        assert not data(await w.call_tool("memory_search", {"query": "té verde", "mode": "keyword"}))["results"]
        with pytest.raises(ToolError):
            await w.call_tool("memory_delete", {"id": nid})
        # edit, history, undo
        await p.call_tool("memory_update", {"id": nid, "text": "Ana prefiere el té negro por la mañana"})
        h = data(await p.call_tool("memory_history", {"id": nid}))
        assert h["previous_versions"][0]["text"].endswith("té verde por la mañana")
        await p.call_tool("memory_undo", {"id": nid})
        assert any("té verde" in i["text"] for i in data(await p.call_tool("memory_list", {}))["items"])
        # shared is visible to both after an explicit promote
        await p.call_tool("memory_promote", {"id": nid, "confirm": True})
        assert any("té verde" in i["text"] for i in data(await w.call_tool("memory_list", {"include_shared": True}))["items"])


async def test_store_refuses_foreign_dataset(tmp_path):
    st = LocalNoteStore(tmp_path, {dataset_id("work")})
    with pytest.raises(MemoryError_):
        await st.remember("hola mundo", dataset_id("personal"))
    assert await st.raw_text(dataset_id("personal"), "x") is None


def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_http_needs_token_and_local_host(home):
    port = _free()
    srv = uvicorn.Server(uvicorn.Config(local.http_app(home, port), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base, tok = f"http://127.0.0.1:{port}", (home / "token").read_text()
    try:
        assert httpx.get(f"{base}/health").status_code == 401
        assert httpx.get(f"{base}/health", headers={"Authorization": f"Bearer {tok}", "Host": "evil.example"}).status_code == 403
        assert httpx.get(f"{base}/health", headers={"Authorization": f"Bearer {tok}"}).json()["mode"] == "local"
        r = httpx.post(f"{base}/personal/mcp", headers={"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream",
                                                       "Content-Type": "application/json"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                           "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and "serverInfo" in r.text
    finally:
        srv.should_exit = True
