"""Passkeys (WebAuthn) as optional second sign-in method, with a software authenticator (soft-webauthn)."""
from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from soft_webauthn import SoftWebauthnDevice

from hub_gateway import local_auth
from test_local_auth import BASE, HASH, PW, probe_mod

u2b = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
b2u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731


def make(passkeys=True):
    prov = local_auth.LocalOAuthProvider(base_url=BASE, user="alex", password_hash=HASH, store=MemoryStore(),
                                         passkeys=passkeys)
    mcp = FastMCP("t", auth=prov)

    @mcp.tool
    def ping() -> str:
        return "pong"

    return mcp.http_app(path="/mcp"), prov


async def start(c):
    meta = (await c.get("/.well-known/oauth-authorization-server")).json()
    reg = (await c.post(meta["registration_endpoint"], json={"redirect_uris": [probe_mod.REDIRECT],
                                                             "token_endpoint_auth_method": "none"})).json()
    _, ch = probe_mod.pkce()
    r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"],
                                          "redirect_uri": probe_mod.REDIRECT, "code_challenge": ch,
                                          "code_challenge_method": "S256", "state": "s"})
    return r.headers["location"].split("txn=")[1]


async def enroll(c, dev, password=PW):
    o = await c.post("/passkey/enroll/begin", json={"username": "alex", "password": password})
    if o.status_code != 200:
        return o
    o = o.json()
    cid = o.pop("cid")
    o["challenge"] = u2b(o["challenge"])
    o["user"]["id"] = u2b(o["user"]["id"])
    a = dev.create({"publicKey": o}, BASE)
    cred = {"id": b2u(a["rawId"]), "rawId": b2u(a["rawId"]), "type": "public-key", "response": {
        "clientDataJSON": b2u(a["response"]["clientDataJSON"]), "attestationObject": b2u(a["response"]["attestationObject"])}}
    return await c.post("/passkey/enroll/finish", json={"cid": cid, "credential": cred, "name": "test"})


async def assertion(c, dev, txn):
    o = (await c.post("/passkey/login/begin", json={"txn": txn})).json()
    o["challenge"] = u2b(o["challenge"])
    a = dev.get({"publicKey": o}, BASE)
    return {"id": b2u(a["rawId"]), "rawId": b2u(a["rawId"]), "type": "public-key", "response": {
        "authenticatorData": b2u(a["response"]["authenticatorData"]), "clientDataJSON": b2u(a["response"]["clientDataJSON"]),
        "signature": b2u(a["response"]["signature"]), "userHandle": b2u(a["response"]["userHandle"])}}


@pytest.mark.asyncio
async def test_enroll_and_sign_in_with_passkey():
    app, _ = make()
    dev = SoftWebauthnDevice()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            txn = await start(c)
            page = await c.get("/local-login", params={"txn": txn})
            assert "pk-login" not in page.text and 'name="password"' in page.text  # nothing enrolled yet
            assert (await enroll(c, dev, "wrong password!")).status_code == 401
            assert (await enroll(c, dev)).json()["passkeys"] == 1
            page = await c.get("/local-login", params={"txn": txn})
            assert "pk-login" in page.text and 'name="password"' in page.text  # password still offered
            assert "script-src 'self'" in page.headers["content-security-policy"]
            cred = await assertion(c, dev, txn)
            r = await c.post("/passkey/login/finish", json={"txn": txn, "credential": cred})
            assert r.status_code == 200, r.text
            r = await c.get(r.json()["next"])
            assert r.status_code == 302 and r.headers["location"].startswith(probe_mod.REDIRECT) and "code=" in r.headers["location"]
            # replay of the same assertion on a new authorization is refused (challenge is single use)
            txn2 = await start(c)
            await c.post("/passkey/login/begin", json={"txn": txn2})
            r = await c.post("/passkey/login/finish", json={"txn": txn2, "credential": cred})
            assert r.status_code == 401
            # continue without a verified passkey does nothing
            assert (await c.get("/passkey/continue", params={"txn": txn2})).status_code == 400


@pytest.mark.asyncio
async def test_wrong_origin_refused():
    app, _ = make()
    dev = SoftWebauthnDevice()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            await enroll(c, dev)
            txn = await start(c)
            o = (await c.post("/passkey/login/begin", json={"txn": txn})).json()
            o["challenge"] = u2b(o["challenge"])
            a = dev.get({"publicKey": o}, "https://evil.example")
            cred = {"id": b2u(a["rawId"]), "rawId": b2u(a["rawId"]), "type": "public-key", "response": {
                k: b2u(v) for k, v in a["response"].items()}}
            assert (await c.post("/passkey/login/finish", json={"txn": txn, "credential": cred})).status_code == 401


@pytest.mark.asyncio
async def test_off_by_default():
    app, _ = make(passkeys=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            assert (await c.get("/passkey/enroll")).status_code == 404
            assert (await c.post("/passkey/login/begin", json={})).status_code in (404, 405)


@pytest.mark.asyncio
async def test_router_passkey_leads_to_consent():
    from hub_gateway.router.auth import RouterOAuthProvider
    from hub_gateway.router.server import build_router
    from test_router_auth import CTXS, settings
    prov = RouterOAuthProvider(base_url=BASE, contexts=CTXS, store=MemoryStore(), user="alex", password_hash=HASH,
                               passkeys=True)
    app = build_router(settings(), provider=prov).http_app(path="/mcp")
    dev = SoftWebauthnDevice()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            await enroll(c, dev)
            txn = await start(c)
            assert "pk-login" in (await c.get("/local-login", params={"txn": txn})).text
            r = await c.post("/passkey/login/finish", json={"txn": txn, "credential": await assertion(c, dev, txn)})
            r = await c.get(r.json()["next"])
            assert r.status_code == 200 and "ctx" in r.text and "/consent" in r.text
