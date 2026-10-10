#!/usr/bin/env python3
"""Scripted MCP OAuth client for a hub that uses the built-in login (HUB_AUTH_PROVIDER=local).

Does what Claude/ChatGPT/Grok/Cursor do: discovery → Dynamic Client Registration → /authorize with PKCE →
login form → code → /token → MCP initialize + tools/list with the bearer → refresh (rotation) → old tokens dead.

    MNEMOS_PROBE_PASSWORD=... python3 scripts/oauth_probe.py http://127.0.0.1:18300 --user alex
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import os
import re
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx

REDIRECT = "http://127.0.0.1:33418/callback"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def pkce() -> tuple[str, str]:
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")


async def mcp_tools(c: httpx.AsyncClient, base: str, token: str) -> tuple[int, list[str]]:
    h = {**ACCEPT, "Authorization": f"Bearer {token}"}
    r = await c.post(f"{base}/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "oauth-probe", "version": "1"}}})
    if r.status_code != 200:
        return r.status_code, []
    sid = r.headers.get("mcp-session-id")
    if sid:
        h["mcp-session-id"] = sid
    await c.post(f"{base}/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r = await c.post(f"{base}/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = re.findall(r'"name":\s*"([a-z_]+)"', r.text)
    return r.status_code, names


async def probe(c: httpx.AsyncClient, base: str, user: str, password: str, log=print,
                contexts: list[str] | None = None, switch: bool = False) -> dict:
    """contexts=None: a hub. contexts=[...]: the router, which shows a consent page after the login."""
    base = base.rstrip("/")
    out: dict = {}
    r = await c.post(f"{base}/mcp", headers=ACCEPT, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    out["unauth_status"] = r.status_code
    meta = (await c.get(f"{base}/.well-known/oauth-authorization-server")).json()
    out["grant_types"] = meta.get("grant_types_supported")
    reg = await c.post(meta["registration_endpoint"], json={
        "client_name": "oauth-probe", "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": "user"})
    out["register_status"] = reg.status_code
    client_id = reg.json()["client_id"]
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(8)
    r = await c.get(meta["authorization_endpoint"], params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state, "scope": "user", "resource": f"{base}/mcp"})
    login_url = r.headers.get("location", "")
    out["authorize_status"], out["login_page"] = r.status_code, urlparse(login_url).path
    txn = parse_qs(urlparse(login_url).query)["txn"][0]
    page = await c.get(login_url)
    out["login_page_names_client"] = "oauth-probe" in page.text
    bad = await c.post(f"{base}/local-login", data={"txn": txn, "username": user, "password": password + "x"})
    out["wrong_password"] = "Wrong user or password" in bad.text and bad.status_code == 200
    ok = await c.post(f"{base}/local-login", data={"txn": txn, "username": user, "password": password})
    if contexts is not None:
        m = re.search(r'name="consent" value="([^"]+)"', ok.text)
        out["consent_page"] = ok.status_code == 200 and bool(m) and "checked" not in ok.text
        if m:
            none = await c.post(f"{base}/consent", data={"consent": m.group(1)})
            out["consent_needs_context"] = "at least one context" in none.text
            ok = await c.post(f"{base}/consent", data={"consent": m.group(1), "ctx": contexts,
                                                        **({"switch": "1"} if switch else {})})
    loc = ok.headers.get("location", "")
    q = parse_qs(urlparse(loc).query)
    out["login_redirect_ok"] = ok.status_code == 302 and loc.startswith(REDIRECT) and q.get("state") == [state]
    code = q["code"][0]
    replay = await c.post(f"{base}/local-login", data={"txn": txn, "username": user, "password": password})
    out["txn_single_use"] = replay.status_code == 400
    tok = await c.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id,
        "code_verifier": verifier, "resource": f"{base}/mcp"})
    out["token_status"] = tok.status_code
    t = tok.json()
    out["scope"] = t.get("scope")
    again = await c.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id,
        "code_verifier": verifier})
    out["code_single_use"] = again.status_code in (400, 401) and "invalid_grant" in again.text
    out["mcp_status"], tools = await mcp_tools(c, base, t["access_token"])
    out["tools"] = len(tools)
    ref = await c.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": client_id})
    out["refresh_status"] = ref.status_code
    t2 = ref.json()
    out["old_access_revoked"] = (await mcp_tools(c, base, t["access_token"]))[0] == 401
    old_ref = await c.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": client_id})
    out["old_refresh_rejected"] = old_ref.status_code in (400, 401)
    out["new_access_works"] = (await mcp_tools(c, base, t2.get("access_token", "")))[0] == 200
    return out


EXPECT = {"unauth_status": 401, "register_status": 201, "authorize_status": 302, "login_page": "/local-login",
          "login_page_names_client": True, "wrong_password": True, "login_redirect_ok": True, "txn_single_use": True,
          "token_status": 200, "code_single_use": True, "mcp_status": 200, "refresh_status": 200,
          "old_access_revoked": True, "old_refresh_rejected": True, "new_access_works": True}


def check(out: dict) -> list[str]:
    bad = [f"{k}: got {out.get(k)!r}, want {v!r}" for k, v in EXPECT.items() if out.get(k) != v]
    for k in ("consent_page", "consent_needs_context"):
        if k in out and out[k] is not True:
            bad.append(f"{k}: got {out[k]!r}")
    if not out.get("tools"):
        bad.append("tools/list returned no tools")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url")
    ap.add_argument("--user", required=True)
    ap.add_argument("--contexts", help="router only: comma-separated contexts to tick on the consent page")
    ap.add_argument("--switch", action="store_true", help="router only: also allow in-chat switching")
    args = ap.parse_args()
    ctxs = [x for x in (args.contexts or "").split(",") if x] or None
    pw = os.environ.get("MNEMOS_PROBE_PASSWORD") or ""
    if not pw:
        raise SystemExit("set MNEMOS_PROBE_PASSWORD")

    async def run() -> dict:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
            return await probe(c, args.base_url, args.user, pw, contexts=ctxs, switch=args.switch)

    out = asyncio.run(run())
    for k, v in out.items():
        print(f"{k:24} {v}")
    bad = check(out)
    print("ALL OK" if not bad else "FAILED:\n  " + "\n  ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
