"""hub-router OAuth (single-connector step 2): sign-in + per-app consent → ctx:* scopes, grants, revoke, IdPs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore

from hub_gateway import local_auth
from hub_gateway.config import ConfigError
from hub_gateway.router.auth import ExternalIdP, RouterOAuthProvider
from hub_gateway.router.config import RouterSettings
from hub_gateway.router.server import build_router

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("oauth_probe", ROOT / "scripts" / "oauth_probe.py")
probe_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_mod)

BASE = "https://mnemos.test"
PW = "correct horse battery"
HASH = local_auth.hash_password(PW, n=2**10)
CTXS = ["work", "personal", "side"]


def settings(**kw):
    return RouterSettings(public_url=BASE, data_dir="/tmp", storage_key="x", contexts=CTXS, **kw)


def router(idps=None, http=None, user="alex", pw=HASH):
    prov = RouterOAuthProvider(base_url=BASE, contexts=CTXS, store=MemoryStore(), user=user, password_hash=pw,
                               idps=idps, http=http)
    mcp = build_router(settings(), provider=prov)
    return mcp.http_app(path="/mcp"), prov


async def run_probe(app, **kw):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            return await probe_mod.probe(c, BASE, "alex", PW, **kw)


@pytest.mark.asyncio
async def test_flow_with_consent_scopes_and_grant():
    app, prov = router()
    out = await run_probe(app, contexts=["personal", "side"])
    assert probe_mod.check(out) == [], out
    assert out["consent_page"] and out["consent_needs_context"]
    assert set(out["scope"].split()) == {"user", "ctx:personal", "ctx:side"}  # no switch unless ticked
    grants = await prov.list_grants()
    assert len(grants) == 1 and grants[0]["contexts"] == ["personal", "side"] and grants[0]["switch"] is False
    assert grants[0]["login"] == "alex" and grants[0]["client_name"] == "oauth-probe"


@pytest.mark.asyncio
async def test_switch_is_opt_in_and_unknown_context_ignored():
    app, _ = router()
    out = await run_probe(app, contexts=["work", "nope"], switch=True)
    assert set(out["scope"].split()) == {"user", "ctx:work", "switch"}


@pytest.mark.asyncio
async def test_revoking_grant_kills_tokens():
    app, prov = router()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            tok = await prov._issue("cid", ["user", "ctx:work", "ctx:side"], None, "alex")
            assert await prov.load_access_token(tok.access_token) is None  # no grant → invalid
            await prov.store.put("cid", {"client_id": "cid", "contexts": ["work"], "switch": False, "login": "alex"},
                                 collection="grants")
            await prov._index_grant("cid", add=True)
            at = await prov.load_access_token(tok.access_token)
            assert at.scopes == ["user", "ctx:work"]  # narrowed to the current grant
            assert await prov.revoke_grant("cid")
            assert await prov.load_access_token(tok.access_token) is None
            assert await prov.list_grants() == []
            r = await c.post("/mcp", headers={**probe_mod.ACCEPT, "authorization": f"Bearer {tok.access_token}"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert r.status_code == 401


def fake_idp(kind, info):
    def handler(req: httpx.Request):
        if req.url.path.endswith(("/token", "/access_token")):
            return httpx.Response(200, json={"access_token": "upstream"})
        assert req.headers["authorization"] == "Bearer upstream"
        return httpx.Response(200, json=info)
    idp = (ExternalIdP.google("gid", "gsec", frozenset({"nico@example.com"})) if kind == "google"
           else ExternalIdP.github("hid", "hsec", frozenset({"nico"})))
    return idp, httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,info,ok", [
    ("google", {"email": "nico@example.com", "email_verified": True}, True),
    ("google", {"email": "nico@example.com", "email_verified": False}, False),
    ("google", {"email": "other@example.com", "email_verified": True}, False),
    ("github", {"login": "Nico"}, True),
    ("github", {"login": "mallory"}, False),
])
async def test_external_signin(kind, info, ok):
    idp, http = fake_idp(kind, info)
    app, prov = router(idps=[idp], http=http, user=None, pw=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            reg = (await c.post("/register", json={"client_name": "x", "redirect_uris": [probe_mod.REDIRECT],
                                                   "token_endpoint_auth_method": "none"})).json()
            _, ch = probe_mod.pkce()
            r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"],
                                                  "redirect_uri": probe_mod.REDIRECT, "code_challenge": ch,
                                                  "code_challenge_method": "S256", "state": "s"})
            txn = parse_qs(urlparse(r.headers["location"]).query)["txn"][0]
            page = await c.get(f"/local-login?txn={txn}")
            assert f"Sign in with {idp.label}" in page.text and 'name="password"' not in page.text
            assert (await c.post("/local-login", data={"txn": txn, "username": "a", "password": "b"})).status_code == 400
            start = await c.get(f"/oidc/{kind}/start", params={"txn": txn})
            up = urlparse(start.headers["location"])
            q = parse_qs(up.query)
            assert up.netloc in ("accounts.google.com", "github.com")
            assert q["redirect_uri"] == [f"{BASE}/oidc/{kind}/callback"]
            cb = await c.get(f"/oidc/{kind}/callback", params={"state": q["state"][0], "code": "c"})
            assert ("Which contexts" in cb.text) is ok
            # state is single use
            again = await c.get(f"/oidc/{kind}/callback", params={"state": q["state"][0], "code": "c"})
            assert again.status_code == 400


def test_settings(monkeypatch):
    monkeypatch.setenv("MNEMOS_ROUTER_PUBLIC_URL", "http://insecure")
    with pytest.raises(ConfigError):
        RouterSettings.from_env()
    monkeypatch.setenv("MNEMOS_ROUTER_PUBLIC_URL", BASE)
    monkeypatch.setenv("MNEMOS_ROUTER_STORAGE_KEY", "k")
    monkeypatch.setenv("MNEMOS_GOOGLE_CLIENT_ID", "g")
    with pytest.raises(ConfigError):  # Google without an allowlist
        RouterSettings.from_env()
    monkeypatch.setenv("MNEMOS_ROUTER_ALLOWED_EMAILS", "Nico@Example.com")
    s = RouterSettings.from_env()
    assert s.allowed_emails == {"nico@example.com"} and s.backends == {}
    with pytest.raises(ValueError):
        RouterOAuthProvider(base_url=BASE, contexts=CTXS, store=MemoryStore())


CLAUDE_DCR = {"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
              "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
              "token_endpoint_auth_method": "none"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "user", "user switch ctx:work ctx:personal ctx:side"])
async def test_claude_ai_registration(scope):
    """Claude.ai refuses to register unless metadata advertises public clients (`none`)."""
    app, _ = router()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
            meta = (await c.get("/.well-known/oauth-authorization-server")).json()
            assert "none" in meta["token_endpoint_auth_methods_supported"]
            assert "S256" in meta["code_challenge_methods_supported"]
            body = {**CLAUDE_DCR, **({"scope": scope} if scope else {})}
            r = await c.post(meta["registration_endpoint"], json=body,
                             headers={"origin": "https://claude.ai"})
            assert r.status_code == 201, r.text
            j = r.json()
            assert j["client_id"] and j["token_endpoint_auth_method"] == "none" and "client_secret" not in j
            assert j["redirect_uris"] == CLAUDE_DCR["redirect_uris"]
            _, ch = probe_mod.pkce()
            a = await c.get("/authorize", params={"response_type": "code", "client_id": j["client_id"],
                                                  "redirect_uri": CLAUDE_DCR["redirect_uris"][0], "code_challenge": ch,
                                                  "code_challenge_method": "S256", "state": "s",
                                                  **({"scope": scope} if scope else {})})
            assert a.status_code == 302 and "/local-login" in a.headers["location"]


def _form_action_allows(csp: str, base: str, location: str) -> bool:
    """Browser rule: CSP form-action also governs the redirect after a form POST."""
    from urllib.parse import urlparse as up
    fas = [d for d in csp.split(";") if d.strip().startswith("form-action")]
    if not fas:  # no form-action: any redirect hop is allowed (Grok Bot/Cursor chain via www.cursor.com)
        return True
    fa = fas[0].split()[1:]
    loc = up(location)
    origin = f"{loc.scheme}://{loc.netloc}"
    return origin in fa or ("'self'" in fa and origin == base)


@pytest.mark.asyncio
async def test_claude_full_browser_flow_reaches_callback():
    """Real stack: DCR → /authorize → login page → login POST → consent POST → 302 to claude.ai allowed by CSP."""
    app, prov = router()
    cb = CLAUDE_DCR["redirect_uris"][0]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                                     headers={"host": "mnemos.test", "x-forwarded-proto": "https"}) as c:
            j = (await c.post("/register", json=CLAUDE_DCR)).json()
            _, ch = probe_mod.pkce()
            a = await c.get("/authorize", params={"response_type": "code", "client_id": j["client_id"], "redirect_uri": cb,
                                                  "code_challenge": ch, "code_challenge_method": "S256", "state": "st",
                                                  "scope": "user", "resource": f"{BASE}/mcp"})
            txn = parse_qs(urlparse(a.headers["location"]).query)["txn"][0]
            page = await c.get(f"/local-login?txn={txn}")
            assert "form-action" not in page.headers["content-security-policy"]
            consent = await c.post("/local-login", data={"txn": txn, "username": "alex", "password": PW})
            import re
            token = re.search(r'name="consent" value="([^"]+)"', consent.text).group(1)
            csp = consent.headers["content-security-policy"]
            done = await c.post("/consent", data={"consent": token, "ctx": ["work", "personal", "side"], "switch": "1"})
            assert done.status_code == 302 and done.headers["location"].startswith(cb + "?")
            q = parse_qs(urlparse(done.headers["location"]).query)
            assert q["state"] == ["st"] and q["code"][0]
            assert _form_action_allows(csp, BASE, done.headers["location"]), csp
            assert (await prov.list_grants())[0]["switch"] is True
            assert "script-src 'self'" in csp and "/consent.js" in consent.text
            assert "disabled" in (await c.get("/consent.js")).text
            # double submit / back button: same redirect while the code is unused, then a friendly page
            again = await c.post("/consent", data={"consent": token, "ctx": ["work"]})
            assert again.status_code == 302 and again.headers["location"] == done.headers["location"]
            from hub_gateway.local_auth import _h
            await prov.store.delete(_h(q["code"][0]), collection="codes")  # code redeemed by the client
            again = await c.post("/consent", data={"consent": token, "ctx": ["work"]})
            assert again.status_code == 200 and "You're connected" in again.text
            assert (await c.post("/consent", data={"consent": "bogus", "ctx": ["work"]})).status_code == 400


def test_redirect_origin_is_csp_safe():
    from hub_gateway.local_auth import redirect_origin
    assert redirect_origin("https://claude.ai/api/mcp/auth_callback") == "https://claude.ai"
    assert redirect_origin("http://127.0.0.1:33418/callback") == "http://127.0.0.1:33418"
    assert redirect_origin("cursor://anysphere/cb") == "cursor:"
    assert redirect_origin("https://a.b; script-src *") == ""


def test_login_and_consent_pages_have_no_form_action():
    from hub_gateway.local_auth import LocalOAuthProvider
    r = LocalOAuthProvider._page("x", form_targets=["https://www.cursor.com"], scripts=True)
    csp = r.headers["content-security-policy"]
    assert "form-action" not in csp and "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
