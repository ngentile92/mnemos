"""Concurrencia en el transporte MCP: nunca una respuesta para el request equivocado (regresión 30/09)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from hub_gateway.app import build_server
from hub_gateway.concurrency import DuplicateRequestIdGuard
from hub_gateway.config import Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from conftest import DATASETS

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_gateway(monkeypatch, tmp_path, skills_repo, policy_file):
    """Gateway real (FastMCP + streamable HTTP + uvicorn) con Cognee falso que tarda 0,6 s por query."""
    monkeypatch.setenv("HUB_CONTEXT", "work")
    monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUB_SKILLS_ROOT", str(skills_repo))
    monkeypatch.setenv("HUB_SECRET_POLICY_FILE", str(policy_file))

    async def cognee(req: httpx.Request):
        q = json.loads(req.content)["query"]
        await asyncio.sleep(0.6)
        return httpx.Response(200, json=[{"text": f"respuesta a {q}", "dataset_id": DATASETS["work"]}])

    mcp = build_server(Settings.from_env(), cognee=CogneeClient("http://cognee", "k", transport=httpx.MockTransport(cognee)),
                       datasets=DatasetMap(DATASETS))
    dups: list = []
    app = DuplicateRequestIdGuard(mcp.http_app(path="/mcp"), on_duplicate=dups.append)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", dups
    srv.should_exit = True


def _payload(r: httpx.Response) -> dict:
    for line in r.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return r.json()


async def _session(c: httpx.AsyncClient) -> dict:
    r = await c.post("/mcp", headers=H, json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    h = {**H, "mcp-session-id": r.headers["mcp-session-id"], "mcp-protocol-version": "2025-06-18"}
    await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return h


async def _search(c, h, rid, q, delay=0.0):
    await asyncio.sleep(delay)
    r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {
        "name": "memory_search", "arguments": {"query": q, "include_shared": False}}})
    return _payload(r)


async def test_many_concurrent_requests_get_their_own_answer(live_gateway):
    base, dups = live_gateway
    async with httpx.AsyncClient(base_url=base, timeout=15) as c:
        h = await _session(c)
        outs = await asyncio.gather(*(_search(c, h, 100 + i, f"pregunta {i}") for i in range(30)))
    for i, out in enumerate(outs):
        assert out["id"] == 100 + i
        assert f"respuesta a pregunta {i}" in out["result"]["content"][0]["text"]
    assert dups == []


async def test_duplicate_inflight_id_gets_its_own_answer_not_swapped(live_gateway):
    """Sin la guarda, B recibía la respuesta de A y A quedaba colgado hasta el timeout (un cliente MCP real)."""
    base, dups = live_gateway
    async with httpx.AsyncClient(base_url=base, timeout=5) as c:
        h = await _session(c)
        a, b, b2 = await asyncio.gather(_search(c, h, 7, "consulta A"), _search(c, h, 7, "consulta B", delay=0.2),
                                        _search(c, h, "7", "consulta B2", delay=0.3))
        again = await _search(c, h, 7, "consulta C")  # terminado A, el id se puede reutilizar sin reasignar
    assert a["id"] == 7 and "respuesta a consulta A" in a["result"]["content"][0]["text"]
    assert b["id"] == 7 and "respuesta a consulta B" in b["result"]["content"][0]["text"]
    assert "consulta A" not in b["result"]["content"][0]["text"]
    assert b2["id"] == "7" and "respuesta a consulta B2" in b2["result"]["content"][0]["text"]
    assert again["id"] == 7 and "respuesta a consulta C" in again["result"]["content"][0]["text"]
    assert dups == [["7"], ["7"]]


async def test_guard_scopes_ids_per_session_and_matches_sdk_str_ids():
    seen = []
    gate = asyncio.Event()

    async def app(scope, receive, send):
        rid = json.loads((await receive())["body"])["id"]
        seen.append(rid)
        await gate.wait()
        out = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", str(len(out)).encode())]})
        await send({"type": "http.response.body", "body": out})

    guard = DuplicateRequestIdGuard(app)

    def call(session: str, rid):
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call"}).encode()
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "method": "POST", "headers": [(b"mcp-session-id", session.encode())]}
        return guard(scope, receive, send), sent

    t1, s1 = call("s1", 1)
    t2, s2 = call("s2", 1)      # otra sesión, mismo id: pasa tal cual
    t3, s3 = call("s1", "1")    # misma sesión, "1" == 1 para el SDK: se reasigna a un id interno
    t4, s4 = call("", 1)        # sin sesión (stateless): cada POST es independiente, no se toca
    t5, s5 = call("", 1)
    tasks = [asyncio.create_task(t) for t in (t1, t2, t3, t4, t5)]
    await asyncio.sleep(0.05)
    assert seen[:2] == [1, 1] and isinstance(seen[2], str) and seen[2].startswith("hub-dup-") and seen[3:] == [1, 1]
    gate.set()
    await asyncio.gather(*tasks)
    ids = [json.loads(s[-1]["body"])["id"] for s in (s1, s2, s3, s4, s5)]
    assert ids == [1, 1, "1", 1, 1]
    cl = dict(s3[0]["headers"])[b"content-length"]
    assert int(cl) == len(s3[-1]["body"])
    assert guard.inflight == set()


async def test_id_restorer_handles_sse_split_across_chunks():
    from hub_gateway.concurrency import _IdRestorer
    out = []

    async def send(m):
        out.append(m)
    r = _IdRestorer(send, "hub-dup-1-abc", 42)
    ev = b'event: message\ndata: {"jsonrpc":"2.0","id":"hub-dup-1-abc","result":{}}\n\n'
    await r({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
    await r({"type": "http.response.body", "body": ev[:40], "more_body": True})   # corta el token al medio
    await r({"type": "http.response.body", "body": ev[40:], "more_body": True})
    await r({"type": "http.response.body", "body": b"", "more_body": False})
    body = b"".join(m.get("body", b"") for m in out[1:])
    assert body == ev.replace(b'"hub-dup-1-abc"', b"42")
