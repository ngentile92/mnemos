"""Built-in OAuth server (HUB_AUTH_PROVIDER=local): full DCR + PKCE + login + refresh flow, in-process."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore

from hub_gateway import local_auth
from hub_gateway.auth import login_from_token

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("oauth_probe", ROOT / "scripts" / "oauth_probe.py")
probe_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_mod)

BASE = "https://testhub.local"
PW = "correct horse battery"
HASH = local_auth.hash_password(PW, n=2**10)  # fast in tests


def make_app():
    prov = local_auth.LocalOAuthProvider(base_url=BASE, user="Alex", password_hash=HASH, store=MemoryStore())
    mcp = FastMCP("t", auth=prov)

    @mcp.tool
    def ping() -> str:
        return "pong"

    return mcp.http_app(path="/mcp"), prov


def test_password_hash_roundtrip():
    assert local_auth.verify_password(PW, HASH)
    assert not local_auth.verify_password(PW + "!", HASH)
    assert not local_auth.verify_password(PW, "garbage")
    assert HASH.startswith("scrypt:") and PW not in HASH


@pytest.mark.asyncio
async def test_full_flow():
    app, _ = make_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            out = await probe_mod.probe(c, BASE, "alex", PW)
    assert probe_mod.check(out) == [], out
    assert "refresh_token" in out["grant_types"]


@pytest.mark.asyncio
async def test_lockout_after_failures():
    app, prov = make_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            for _ in range(local_auth.MAX_FAILS):
                prov._fail()
            assert prov._locked()
            # even the right password is refused while locked
            meta = (await c.get("/.well-known/oauth-authorization-server")).json()
            reg = (await c.post(meta["registration_endpoint"], json={"redirect_uris": [probe_mod.REDIRECT],
                                                                     "token_endpoint_auth_method": "none"})).json()
            _, ch = probe_mod.pkce()
            r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"],
                                                  "redirect_uri": probe_mod.REDIRECT, "code_challenge": ch,
                                                  "code_challenge_method": "S256", "state": "s"})
            txn = r.headers["location"].split("txn=")[1]
            r = await c.post("/local-login", data={"txn": txn, "username": "alex", "password": PW})
            assert r.status_code == 200 and "Too many attempts" in r.text


@pytest.mark.asyncio
async def test_token_claims_feed_owner_check():
    _, prov = make_app()
    tok = await prov._issue("cid", ["user"], None)
    at = await prov.load_access_token(tok.access_token)
    assert login_from_token(at) == "alex"
    stored = await prov.store.get(local_auth._h(tok.access_token), collection="access")
    assert stored and tok.access_token not in str(stored)  # only the hash is a key, token never stored


def test_login_page_is_not_frameable():
    r = local_auth.LocalOAuthProvider._page("x")
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_settings_local(monkeypatch):
    from hub_gateway.config import ConfigError, Settings
    base = {"HUB_CONTEXT": "personal", "HUB_PUBLIC_URL": "https://hub-personal.t.ts.net", "COGNEE_API_KEY": "k",
            "HUB_STORAGE_ENCRYPTION_KEY": "x" * 44, "HUB_AUTH_PROVIDER": "local", "HUB_LOCAL_USER": "Alex",
            "HUB_LOCAL_PASSWORD_HASH": HASH}
    for k in ("HUB_ALLOWED_GITHUB_LOGINS", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "HUB_JWT_SIGNING_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.auth_provider == "local" and s.allowed_logins == frozenset({"alex"})
    monkeypatch.delenv("HUB_LOCAL_PASSWORD_HASH")
    with pytest.raises(ConfigError, match="HUB_LOCAL_PASSWORD_HASH"):
        Settings.from_env()
    monkeypatch.setenv("HUB_AUTH_PROVIDER", "github")  # default path still demands the GitHub app
    with pytest.raises(ConfigError, match="GITHUB_CLIENT_ID"):
        Settings.from_env()
