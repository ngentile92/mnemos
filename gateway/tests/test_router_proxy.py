"""Router tool proxy (single-connector step 3): context arg, scope check, forwarding with provenance."""
from __future__ import annotations

import contextlib
import json

import httpx
import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore

from hub_gateway import app as app_mod, local_auth
from hub_gateway.audit import Audit
from hub_gateway.config import Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from hub_gateway.router.auth import RouterOAuthProvider
from hub_gateway.router.config import Backend, RouterSettings
from hub_gateway.router.proxy import Forwarder
from hub_gateway.router.server import build_router
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS
from test_server import CogneeRecorder

BASE = "https://mnemos.test"
KEY = "i" * 40
ACCEPT = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _inner(a):
    while not hasattr(a, "router"):
        a = a.app
    return a


@pytest.fixture
def stack(monkeypatch, tmp_path, skills_repo, policy_file):
    return contextlib.asynccontextmanager(lambda: _stack(monkeypatch, tmp_path, skills_repo, policy_file))


async def _stack(monkeypatch, tmp_path, skills_repo, policy_file):
    gws, recs = {}, {}
    for ctx in ("personal", "work"):
        monkeypatch.setenv("HUB_CONTEXT", ctx)
        monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
        monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / ctx))
        rec = CogneeRecorder()
        mcp = app_mod.build_server(
            Settings.from_env(),
            cognee=CogneeClient("http://c", "k", transport=httpx.MockTransport(lambda r, rec=rec: rec.handler(r))),
            datasets=DatasetMap(DATASETS), skills=SkillIndex(skills_repo, ctx),
            broker=SecretBroker(load_policy(str(policy_file), ctx), None), audit=Audit(ctx, str(tmp_path / f"{ctx}.log")))
        gws[ctx], recs[ctx] = app_mod._internal_app(mcp, KEY), rec

    def factory(b):
        def make(**kw):
            kw.pop("timeout", None)
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=gws[b.context]), timeout=30, **kw)
        return make

    backends = {c: Backend(c, f"http://ts-{c}:8100/mcp", KEY) for c in gws}
    prov = RouterOAuthProvider(base_url=BASE, contexts=["work", "personal", "side"], store=MemoryStore(),
                               user="alex", password_hash=local_auth.hash_password("x" * 12, n=2**10))
    s = RouterSettings(public_url=BASE, data_dir=str(tmp_path), storage_key="k",
                       contexts=["work", "personal", "side"], backends=backends)
    router = build_router(s, provider=prov, forwarder=Forwarder(backends, factory))
    rapp = router.http_app(path="/mcp")
    async with contextlib.AsyncExitStack() as st:
        for a in [*gws.values()]:
            await st.enter_async_context(_inner(a).router.lifespan_context(_inner(a)))
        await st.enter_async_context(rapp.router.lifespan_context(rapp))
        c = await st.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=rapp), base_url=BASE))
        yield c, prov, recs, router


async def bearer(prov, contexts, switch=False):
    await prov.store.put("cid", {"client_id": "cid", "contexts": contexts, "switch": switch, "login": "alex"},
                         collection="grants")
    tok = await prov._issue("cid", ["user", *(f"ctx:{c}" for c in contexts), *(["switch"] if switch else [])],
                            None, "alex")
    return tok.access_token


async def mcp_session(c, token):
    h = {**ACCEPT, "authorization": f"Bearer {token}"}
    r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Cursor", "version": "2"}}})
    h["mcp-session-id"] = r.headers["mcp-session-id"]
    await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call(method, params, rid=[1]):
        rid[0] += 1
        r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": rid[0], "method": method, "params": params})
        return [json.loads(x[5:]) for x in r.text.splitlines() if x.startswith("data:")][-1]
    return call


async def wait_tools(call):
    import asyncio
    for _ in range(50):
        names = [t["name"] for t in (await call("tools/list", {}))["result"]["tools"]]
        if "memory_save" in names:
            return (await call("tools/list", {}))["result"]["tools"]
        await asyncio.sleep(0.1)
    raise AssertionError(names)


@pytest.mark.asyncio
async def test_proxy_routes_only_granted_contexts(stack):
    async with stack() as (c, prov, recs, _):
        call = await mcp_session(c, await bearer(prov, ["personal"]))
        tools = {t["name"]: t for t in await wait_tools(call)}
        save = tools["memory_save"]["inputSchema"]
        assert "context" not in save["required"] and save["properties"]["context"]["enum"] == ["work", "personal", "side"]
        assert "hub_use_context" not in tools  # no `switch` scope → hidden
        assert "secret_http_request" in tools and sum(n == "hub_whoami" for n in tools) == 1

        ok = await call("tools/call", {"name": "memory_save", "arguments": {"context": "personal", "text": "via router"}})
        assert not ok["result"].get("isError"), ok
        assert any("via router" in b for b in recs["personal"].remembers) and not recs["work"].remembers
        assert any("Cursor via mnemos" in b for b in recs["personal"].remembers)  # provenance app

        denied = await call("tools/call", {"name": "memory_save", "arguments": {"context": "work", "text": "nope"}})
        assert denied["result"]["isError"] and "not granted" in json.dumps(denied)
        assert not recs["work"].remembers
        single = await call("tools/call", {"name": "memory_save", "arguments": {"text": "only one context"}})
        assert not single["result"].get("isError") and any("only one context" in b for b in recs["personal"].remembers)

        who = await call("tools/call", {"name": "hub_whoami", "arguments": {}})
        assert who["result"]["structuredContent"] == {"login": "alex", "contexts": ["personal"], "can_switch": False}


@pytest.mark.asyncio
async def test_granted_but_no_backend(stack):
    async with stack() as (c, prov, _, _):
        call = await mcp_session(c, await bearer(prov, ["side"]))
        await wait_tools(call)
        r = await call("tools/call", {"name": "memory_save", "arguments": {"context": "side", "text": "x y z"}})
        assert r["result"]["isError"] and "no gateway" in json.dumps(r)


@pytest.mark.asyncio
async def test_unauthenticated_router_is_401(stack):
    async with stack() as (c, *_):
        r = await c.post("/mcp", headers=ACCEPT, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_switch_within_granted_contexts(stack):
    async with stack() as (c, prov, recs, _):
        call = await mcp_session(c, await bearer(prov, ["personal", "work"], switch=True))
        tools = {t["name"] for t in await wait_tools(call)}
        assert "hub_use_context" in tools
        amb = await call("tools/call", {"name": "memory_save", "arguments": {"text": "which one?"}})
        assert amb["result"]["isError"] and "hub_use_context" in json.dumps(amb)
        bad = await call("tools/call", {"name": "hub_use_context", "arguments": {"context": "side"}})
        assert bad["result"]["isError"]
        ok = await call("tools/call", {"name": "hub_use_context", "arguments": {"context": "work"}})
        assert ok["result"]["structuredContent"] == {"active_context": "work"}
        await call("tools/call", {"name": "memory_save", "arguments": {"text": "goes to work"}})
        assert any("goes to work" in b for b in recs["work"].remembers)
        who = await call("tools/call", {"name": "hub_whoami", "arguments": {}})
        assert who["result"]["structuredContent"]["active_context"] == "work"
        # explicit context still wins, and still only among granted ones
        await call("tools/call", {"name": "memory_save", "arguments": {"context": "personal", "text": "explicit"}})
        assert any("explicit" in b for b in recs["personal"].remembers)


@pytest.mark.asyncio
async def test_switch_tool_refused_without_scope(stack):
    async with stack() as (c, prov, _, _):
        call = await mcp_session(c, await bearer(prov, ["personal", "work"]))
        await wait_tools(call)
        r = await call("tools/call", {"name": "hub_use_context", "arguments": {"context": "work"}})
        assert "error" in r or r["result"]["isError"]
        amb = await call("tools/call", {"name": "memory_save", "arguments": {"text": "which one?"}})
        assert amb["result"]["isError"] and "hub_use_context" not in json.dumps(amb)
