"""Router OAuth: who you are (local password, Google OIDC or GitHub) + which contexts THIS app may use.

Flow: /authorize → /local-login (sign in with one of the enabled methods) → consent page with one checkbox per
context (all unchecked) and an opt-in "switch" checkbox → authorization code whose scopes are
`user ctx:<a> ctx:<b> [switch]`. The decision is stored as a *grant* per OAuth client (app), which the dashboard
lists and can revoke; a token is only valid while its grant exists and only for contexts still in it.
"""

from __future__ import annotations

import html
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ..local_auth import EXPIRED, LocalOAuthProvider, _h, redirect_origin

SWITCH = "switch"
OIDC_STATE_TTL = 10 * 60


def ctx_scope(name: str) -> str:
    return f"ctx:{name}"


def granted_contexts(scopes: list[str] | None) -> list[str]:
    return [s[4:] for s in (scopes or []) if s.startswith("ctx:")]


@dataclass(frozen=True)
class ExternalIdP:
    """An upstream sign-in (Google OIDC or GitHub OAuth app). Only identity is taken from it."""

    name: str          # "google" | "github"
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    allowed: frozenset[str]   # allowed emails (google) / logins (github), lowercase

    @classmethod
    def google(cls, client_id: str, client_secret: str, allowed: frozenset[str]) -> "ExternalIdP":
        return cls("google", "Google", client_id, client_secret, "https://accounts.google.com/o/oauth2/v2/auth",
                   "https://oauth2.googleapis.com/token", "https://openidconnect.googleapis.com/v1/userinfo",
                   "openid email", allowed)

    @classmethod
    def github(cls, client_id: str, client_secret: str, allowed: frozenset[str]) -> "ExternalIdP":
        return cls("github", "GitHub", client_id, client_secret, "https://github.com/login/oauth/authorize",
                   "https://github.com/login/oauth/access_token", "https://api.github.com/user", "read:user", allowed)

    def identity(self, info: dict) -> str | None:
        if self.name == "google":
            if not info.get("email_verified"):
                return None
            ident = str(info.get("email") or "").lower()
        else:
            ident = str(info.get("login") or "").lower()
        return ident if ident and ident in self.allowed else None


class RouterOAuthProvider(LocalOAuthProvider):
    def __init__(self, *, base_url: str, contexts: list[str], store: Any, user: str | None = None,
                 password_hash: str | None = None, idps: list[ExternalIdP] | None = None,
                 http: httpx.AsyncClient | None = None, passkeys: bool | None = None) -> None:
        self.contexts = list(contexts)
        self.idps = {i.name: i for i in (idps or [])}
        self.local_enabled = bool(user and (password_hash or "").startswith("scrypt:"))
        if not self.local_enabled and not self.idps:
            raise ValueError("router needs a sign-in method: HUB_LOCAL_USER + HUB_LOCAL_PASSWORD_HASH, "
                             "Google (MNEMOS_GOOGLE_CLIENT_ID/SECRET) or GitHub (MNEMOS_ROUTER_GITHUB_CLIENT_ID/SECRET)")
        super().__init__(base_url=base_url, user=user or "", password_hash=password_hash or "", store=store, passkeys=passkeys,
                         resource_name="Mnemos", require_password=False,
                         scopes=["user", SWITCH, *map(ctx_scope, self.contexts)])
        self.http = http

    def _base(self) -> str:
        return str(self.base_url).rstrip("/")

    # ------------------------------------------------------------ sign-in page
    async def _login_form(self, txn: str, pending: dict, error: str = "") -> Response:
        client = await self.get_client(pending["client_id"])
        name = html.escape((client.client_name if client else None) or pending["client_id"])
        err = f'<p class="e">{html.escape(error)}</p>' if error else ""
        t = html.escape(txn)
        parts = [f"<h2>Mnemos</h2><p><b>{name}</b> wants to connect. Sign in, then choose which contexts it may use.</p>{err}"]
        if self.local_enabled:
            parts.append(f"""<form method="post" action="/local-login"><input type="hidden" name="txn" value="{t}">
<input name="username" autocomplete="username" placeholder="user" required>
<input name="password" type="password" autocomplete="current-password" placeholder="password" required>
<button type="submit">Sign in</button></form>""")
        for idp in self.idps.values():
            parts.append(f'<form method="get" action="/oidc/{idp.name}/start"><input type="hidden" name="txn" value="{t}">'
                         f'<button type="submit">Sign in with {html.escape(idp.label)}</button></form>')
        pk = await self.passkey_button(txn) if self.local_enabled else ""
        parts.append(pk)
        parts.append('<p class="m">Not you, or you did not start this? Just close this tab.</p>')
        targets = [redirect_origin(pending["redirect_uri"])] + [redirect_origin(i.authorize_url) for i in self.idps.values()]
        return self._page("".join(parts), form_targets=targets, scripts=bool(pk))

    async def login_post(self, request: Request) -> Response:
        if not self.local_enabled:
            return self._page("<p class='e'>Password sign-in is disabled.</p>", 400)
        return await super().login_post(request)

    # ------------------------------------------------------------ external sign-in (Google / GitHub)
    async def oidc_start(self, request: Request) -> Response:
        idp = self.idps.get(request.path_params["idp"])
        txn = request.query_params.get("txn", "")
        if not idp or not txn or not await self.store.get(txn, collection="pending"):
            return self._page(EXPIRED, 400)
        state = secrets.token_urlsafe(24)
        await self.store.put(_h(state), {"txn": txn, "idp": idp.name}, collection="oidc", ttl=OIDC_STATE_TTL)
        q = {"client_id": idp.client_id, "redirect_uri": f"{self._base()}/oidc/{idp.name}/callback",
             "response_type": "code", "scope": idp.scope, "state": state}
        if idp.name == "google":
            q["prompt"] = "select_account"
        return RedirectResponse(f"{idp.authorize_url}?{urlencode(q)}", status_code=302)

    async def oidc_callback(self, request: Request) -> Response:
        idp = self.idps.get(request.path_params["idp"])
        state = request.query_params.get("state", "")
        st = await self.store.get(_h(state), collection="oidc") if state else None
        if not idp or not st or st["idp"] != idp.name:
            return self._page(EXPIRED, 400)
        await self.store.delete(_h(state), collection="oidc")
        pending = await self.store.get(st["txn"], collection="pending")
        if not pending:
            return self._page(EXPIRED, 400)
        if self._locked():
            return await self._login_form(st["txn"], pending, "Too many attempts. Try again in a few minutes.")
        ident = None
        try:
            http = self.http or httpx.AsyncClient(timeout=15)
            tok = await http.post(idp.token_url, headers={"accept": "application/json"}, data={
                "client_id": idp.client_id, "client_secret": idp.client_secret, "grant_type": "authorization_code",
                "code": request.query_params.get("code", ""),
                "redirect_uri": f"{self._base()}/oidc/{idp.name}/callback"})
            access = tok.json().get("access_token") if tok.status_code == 200 else None
            if access:
                info = await http.get(idp.userinfo_url, headers={"authorization": f"Bearer {access}",
                                                                 "accept": "application/json"})
                ident = idp.identity(info.json()) if info.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            ident = None
        if not ident:
            self._fail()
            return await self._login_form(st["txn"], pending, f"That {idp.label} account is not allowed here.")
        return await self._after_login(st["txn"], pending, ident)

    # ------------------------------------------------------------ consent
    async def _after_login(self, txn: str, pending: dict, login: str) -> Response:
        consent = secrets.token_urlsafe(24)
        await self.store.delete(txn, collection="pending")
        await self.store.put(_h(consent), {**pending, "login": login}, collection="consent", ttl=OIDC_STATE_TTL)
        return await self._consent_form(consent, pending)

    async def _consent_form(self, consent: str, pending: dict, error: str = "") -> Response:
        client = await self.get_client(pending["client_id"])
        name = html.escape((client.client_name if client else None) or pending["client_id"])
        err = f'<p class="e">{html.escape(error)}</p>' if error else ""
        boxes = "".join(f'<label><input type="checkbox" name="ctx" value="{html.escape(c)}"> {html.escape(c)}</label><br>'
                        for c in self.contexts)
        return self._page(form_targets=[redirect_origin(pending["redirect_uri"])], body=f"""<h2>Mnemos</h2><p>Which contexts may <b>{name}</b> use? It will not see the others.</p>{err}
<form method="post" action="/consent"><input type="hidden" name="consent" value="{html.escape(consent)}">
<fieldset><legend>Contexts</legend>{boxes}</fieldset>
<label><input type="checkbox" name="switch" value="1"> Let it switch between these contexts during a chat</label>
<button type="submit">Allow</button></form><p class="m">You can revoke this app later from the dashboard.</p>""")

    async def consent_post(self, request: Request) -> Response:
        form = await request.form()
        consent = str(form.get("consent", ""))
        pending = await self.store.get(_h(consent), collection="consent") if consent else None
        if not pending:
            return self._page(EXPIRED, 400)
        chosen = [c for c in self.contexts if c in set(map(str, form.getlist("ctx")))]
        if not chosen:
            return await self._consent_form(consent, pending, "Choose at least one context.")
        await self.store.delete(_h(consent), collection="consent")
        switch = form.get("switch") == "1"
        client = await self.get_client(pending["client_id"])
        await self.store.put(pending["client_id"], {
            "client_id": pending["client_id"], "client_name": (client.client_name if client else None) or "",
            "login": pending["login"], "contexts": chosen, "switch": switch, "created": int(time.time())},
            collection="grants")
        await self._index_grant(pending["client_id"], add=True)
        scopes = ["user", *map(ctx_scope, chosen), *([SWITCH] if switch else [])]
        return await self._grant_code("", pending, pending["login"], scopes)

    async def _grant_code(self, txn: str, pending: dict, login: str, scopes: list[str]) -> Response:
        if txn:
            return await super()._grant_code(txn, pending, login, scopes)
        # consent path: pending already consumed
        from mcp.server.auth.provider import construct_redirect_uri

        code = secrets.token_urlsafe(32)
        await self.store.put(_h(code), {**pending, "scopes": scopes, "login": login,
                                        "expires_at": time.time() + 300}, collection="codes", ttl=300)
        return RedirectResponse(construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]),
                                status_code=302)

    # ------------------------------------------------------------ grants (per app)
    async def _index_grant(self, client_id: str, add: bool) -> None:
        idx = (await self.store.get("index", collection="grant-index")) or {"ids": []}
        ids = [i for i in idx["ids"] if i != client_id] + ([client_id] if add else [])
        await self.store.put("index", {"ids": ids}, collection="grant-index")

    async def list_grants(self) -> list[dict]:
        idx = (await self.store.get("index", collection="grant-index")) or {"ids": []}
        out = []
        for cid in idx["ids"]:
            g = await self.store.get(cid, collection="grants")
            if g:
                out.append(g)
        return out

    async def revoke_grant(self, client_id: str) -> bool:
        g = await self.store.get(client_id, collection="grants")
        await self.store.delete(client_id, collection="grants")
        await self._index_grant(client_id, add=False)
        return bool(g)

    async def load_access_token(self, token: str):  # type: ignore[override]
        at = await super().load_access_token(token)
        if at is None:
            return None
        g = await self.store.get(at.client_id, collection="grants")
        if not g:
            return None  # grant revoked → every token of that app stops working
        allowed = set(map(ctx_scope, g["contexts"])) | {"user"} | ({SWITCH} if g.get("switch") else set())
        at.scopes = [s for s in at.scopes if s in allowed]
        return at

    async def load_refresh_token(self, client, refresh_token: str):
        if not await self.store.get(client.client_id, collection="grants"):
            return None
        return await super().load_refresh_token(client, refresh_token)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        routes += [Route("/consent", self.consent_post, methods=["POST"]),
                   Route("/oidc/{idp}/start", self.oidc_start, methods=["GET"]),
                   Route("/oidc/{idp}/callback", self.oidc_callback, methods=["GET"])]
        return routes
