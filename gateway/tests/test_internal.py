"""Internal listener (single-connector step 1): key-gated, no OAuth, router identity; public cannot forge it."""
from __future__ import annotations

import contextlib
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from hub_gateway import app as app_mod, internal, local_auth
from hub_gateway.audit import Audit
from hub_gateway.config import ConfigError, Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS

KEY = "k" * 40
ACCEPT = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def apps(monkeypatch, tmp_path, skills_repo, policy_file):
    env = {"HUB_CONTEXT": "personal", "HUB_DATA_DIR": str(tmp_path / "d"), "HUB_PUBLIC_URL": "https://h.test",
           "HUB_AUTH_PROVIDER": "local", "HUB_LOCAL_USER": "alex",
           "HUB_LOCAL_PASSWORD_HASH": local_auth.hash_password("pw-pw-pw-pw", n=2**10),
           "HUB_JWT_SIGNING_KEY": "j" * 40, "HUB_STORAGE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
           "HUB_INTERNAL_KEY": KEY, "COGNEE_API_KEY": "ck"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    mcp = app_mod.build_server(
        s, cognee=CogneeClient("http://c", "k", transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        datasets=DatasetMap(DATASETS), skills=SkillIndex(skills_repo, "personal"),
        broker=SecretBroker(load_policy(str(policy_file), "personal"), None), audit=Audit("personal", str(tmp_path / "a")))
    return app_mod._public(mcp, Audit("personal", str(tmp_path / "a"))), app_mod._internal_app(mcp, KEY)


def _inner(a):
    while not hasattr(a, "router"):
        a = a.app
    return a


async def rpc(c, method, params, sid=None, rid=1, **hdr):
    h = {**ACCEPT, **hdr, **({"mcp-session-id": sid} if sid else {})}
    r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    data = None
    for line in r.text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[5:])
    if data is None and r.headers.get("content-type", "").startswith("application/json"):
        data = r.json()
    return r, data


async def session(c, **hdr):
    r, _ = await rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "router", "version": "1"}}, **hdr)
    sid = r.headers.get("mcp-session-id")
    await c.post("/mcp", headers={**ACCEPT, **hdr, "mcp-session-id": sid},
                 json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return r, sid


@pytest.mark.asyncio
async def test_internal_requires_key_and_carries_identity(apps):
    pub, inn = apps
    async with contextlib.AsyncExitStack() as st:
        for a in (pub, inn):
            await st.enter_async_context(_inner(a).router.lifespan_context(_inner(a)))
        c = await st.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=inn), base_url="http://x"))
        assert (await c.post("/mcp", headers=ACCEPT, json={})).status_code == 401
        assert (await c.post("/mcp", headers={**ACCEPT, "x-mnemos-internal": "nope"}, json={})).status_code == 401
        assert (await c.post("/mcp", headers={**ACCEPT, "x-mnemos-internal": KEY, "x-mnemos-verified": "forged"},
                             json={})).status_code != 401  # passes the gate; forged marker is replaced
        hdr = {"x-mnemos-internal": KEY, "x-mnemos-login": "alex", "x-mnemos-app": "Cursor 1.0"}
        r, sid = await session(c, **hdr)
        assert r.status_code == 200 and sid
        _, out = await rpc(c, "tools/call", {"name": "hub_whoami", "arguments": {}}, sid, 2, **hdr)
        body = out["result"]["structuredContent"]
        assert body["login"] == "alex" and body["context"] == "personal"


@pytest.mark.asyncio
async def test_public_cannot_forge_internal_headers(apps):
    pub, inn = apps
    async with contextlib.AsyncExitStack() as st:
        for a in (pub, inn):
            await st.enter_async_context(_inner(a).router.lifespan_context(_inner(a)))
        c = await st.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=pub), base_url="https://h.test"))
        forged = {"x-mnemos-internal": KEY, "x-mnemos-verified": internal.NONCE, "x-mnemos-login": "alex"}
        r = await c.post("/mcp", headers={**ACCEPT, **forged}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                                       "params": {}})
        assert r.status_code == 401  # still needs OAuth


def test_caller_outside_request_is_none():
    assert internal.caller() is None


def test_gate_rejects_short_key_and_settings_validate(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        internal.InternalKeyGate(lambda *a: None, "short")
    monkeypatch.setenv("HUB_CONTEXT", "personal")
    monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
    monkeypatch.setenv("HUB_INTERNAL_KEY", "short")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_default_bind_never_tailnet(monkeypatch):
    monkeypatch.setattr(internal.socket, "gethostbyname", lambda h: "100.64.1.2")
    assert internal.default_bind() == "127.0.0.1"
    monkeypatch.setattr(internal.socket, "gethostbyname", lambda h: "172.18.0.5")
    assert internal.default_bind() == "172.18.0.5"
