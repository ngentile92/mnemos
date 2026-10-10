"""Built-in OAuth 2.1 authorization server: one local user + password, no GitHub OAuth app needed.

    HUB_AUTH_PROVIDER=local
    HUB_LOCAL_USER=alex
    HUB_LOCAL_PASSWORD_HASH=scrypt:...      # python -m hub_gateway.local_auth hash

MCP clients (Claude, ChatGPT, Grok, Cursor) use it like any OAuth server: discovery
(/.well-known/oauth-authorization-server), Dynamic Client Registration (/register), /authorize with PKCE S256,
/token (authorization_code + refresh_token with rotation). /authorize sends the browser to /local-login, a
login + consent page that names the client and where it will redirect.

Tokens are opaque random strings; only their SHA-256 is used as storage key (store encrypted at rest, like the
GitHub provider's). Access tokens last 1 h, refresh tokens 30 days. Failed logins are throttled
(5 per 10 min → locked 10 min).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import sys
import time
from typing import Any
from urllib.parse import urlparse

from fastmcp.server.auth.auth import AccessToken, ClientRegistrationOptions, OAuthProvider, RevocationOptions
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

CODE_TTL = 5 * 60
PENDING_TTL = 10 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 30 * 24 * 3600
MAX_FAILS, FAIL_WINDOW, LOCK_S = 5, 600, 600
SCOPES = ["user"]
_MAXMEM = 128 * 1024 * 1024


def _b64(x: bytes) -> str:
    return base64.urlsafe_b64encode(x).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, *, n: int = 2**15, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{_b64(salt)}:{_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, dk = encoded.split(":")
        if algo != "scrypt":
            return False
        want = _unb64(dk)
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=_MAXMEM, dklen=len(want))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


EXPIRED = "<p class='e'>This sign-in link expired. Start again from your assistant.</p>"


class LocalOAuthProvider(OAuthProvider):
    def __init__(self, *, base_url: str, user: str, password_hash: str, store: Any,
                 resource_name: str = "Mnemos", scopes: list[str] | None = None,
                 require_password: bool = True) -> None:
        if require_password and (not user or not (password_hash or "").startswith("scrypt:")):
            raise ValueError("HUB_LOCAL_USER and HUB_LOCAL_PASSWORD_HASH (scrypt:...) are required")
        self.valid_scopes = list(scopes or SCOPES)
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=self.valid_scopes,
                                                                  default_scopes=SCOPES),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=SCOPES,
        )
        self.user = (user or "").lower()
        self.password_hash = password_hash
        self.store = store
        self.resource_name = resource_name
        self._fails: list[float] = []
        self._locked_until = 0.0

    # ------------------------------------------------------------ clients (DCR)
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await self.store.get(client_id, collection="clients")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required")
        if client_info.scope:
            bad = set(client_info.scope.split()) - set(self.valid_scopes)
            if bad:
                raise ValueError(f"Requested scopes are not valid: {', '.join(sorted(bad))}")
        await self.store.put(client_info.client_id, client_info.model_dump(mode="json"), collection="clients")

    # ------------------------------------------------------------ authorize → login page
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if client.client_id is None:
            raise AuthorizeError(error="invalid_request", error_description="client_id required")
        txn = secrets.token_urlsafe(24)
        await self.store.put(txn, {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "scopes": [s for s in (params.scopes or SCOPES) if s in SCOPES] or SCOPES,
            "resource": params.resource,
        }, collection="pending", ttl=PENDING_TTL)
        return f"{str(self.base_url).rstrip('/')}/local-login?txn={txn}"

    def _locked(self) -> bool:
        now = time.time()
        self._fails = [t for t in self._fails if now - t < FAIL_WINDOW]
        return now < self._locked_until

    def _fail(self) -> None:
        self._fails.append(time.time())
        if len(self._fails) >= MAX_FAILS:
            self._locked_until = time.time() + LOCK_S
            self._fails.clear()

    @staticmethod
    def _page(body: str, code: int = 200) -> HTMLResponse:
        doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Mnemos · sign in</title><style>body{{font:16px system-ui;max-width:420px;margin:10vh auto;padding:0 16px;color:#111}}
input,button{{font:inherit;width:100%;padding:10px;margin:6px 0;box-sizing:border-box}}button{{background:#111;color:#fff;border:0;border-radius:6px}}
.m{{color:#555;font-size:14px}}.e{{color:#b00}}code{{background:#eee;padding:1px 4px}}</style></head><body>{body}</body></html>"""
        return HTMLResponse(doc, status_code=code, headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
            "X-Frame-Options": "DENY", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def _login_form(self, txn: str, pending: dict, error: str = "") -> HTMLResponse:
        client = await self.get_client(pending["client_id"])
        name = html.escape((client.client_name if client else None) or pending["client_id"])
        dest = html.escape(urlparse(pending["redirect_uri"]).netloc or pending["redirect_uri"])
        err = f'<p class="e">{html.escape(error)}</p>' if error else ""
        return self._page(f"""<h2>{html.escape(self.resource_name)}</h2>
<p><b>{name}</b> wants to use your memory, skills and credentials. After signing in you return to <code>{dest}</code>.</p>
{err}<form method="post" action="/local-login"><input type="hidden" name="txn" value="{html.escape(txn)}">
<input name="username" autocomplete="username" placeholder="user" required>
<input name="password" type="password" autocomplete="current-password" placeholder="password" required>
<button type="submit">Allow</button></form><p class="m">Not you, or you did not start this? Just close this tab.</p>""")

    async def login_get(self, request: Request) -> Response:
        txn = request.query_params.get("txn", "")
        pending = await self.store.get(txn, collection="pending") if txn else None
        if not pending:
            return self._page(EXPIRED, 400)
        return await self._login_form(txn, pending)

    async def login_post(self, request: Request) -> Response:
        form = await request.form()
        txn = str(form.get("txn", ""))
        pending = await self.store.get(txn, collection="pending") if txn else None
        if not pending:
            return self._page(EXPIRED, 400)
        if self._locked():
            return await self._login_form(txn, pending, "Too many attempts. Try again in a few minutes.")
        user = str(form.get("username", "")).strip().lower()
        ok_pw = verify_password(str(form.get("password", "")), self.password_hash or "")  # always computed (timing)
        if not (ok_pw and hmac.compare_digest(user.encode(), self.user.encode())):
            self._fail()
            return await self._login_form(txn, pending, "Wrong user or password.")
        return await self._after_login(txn, pending, self.user)

    async def _after_login(self, txn: str, pending: dict, login: str) -> Response:
        """Identity confirmed: hand out the authorization code (the router overrides this to show consent)."""
        return await self._grant_code(txn, pending, login, pending["scopes"])

    async def _grant_code(self, txn: str, pending: dict, login: str, scopes: list[str]) -> Response:
        await self.store.delete(txn, collection="pending")  # single use
        code = secrets.token_urlsafe(32)
        await self.store.put(_h(code), {**pending, "scopes": scopes, "login": login,
                                        "expires_at": time.time() + CODE_TTL}, collection="codes", ttl=CODE_TTL)
        return RedirectResponse(construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]),
                                status_code=302)

    def _public_client_metadata(self, routes: list) -> list:
        """Advertise what /token really accepts: public clients with PKCE (`none`), like the GitHub hubs.
        The SDK default (client_secret_post/basic only) makes Claude.ai refuse to register at all."""
        from mcp.server.auth.handlers.metadata import MetadataHandler
        from mcp.server.auth.routes import build_metadata, cors_middleware

        out = []
        for r in routes:
            if isinstance(r, Route) and r.path == "/.well-known/oauth-authorization-server":
                md = build_metadata(self.base_url, self.service_documentation_url, self.client_registration_options,
                                    self.revocation_options)
                md.issuer = self.issuer_url
                md.token_endpoint_auth_methods_supported = ["none", "client_secret_post", "client_secret_basic"]
                r = Route(r.path, endpoint=cors_middleware(MetadataHandler(md).handle, ["GET", "OPTIONS"]),
                          methods=r.methods or ["GET", "OPTIONS"], name=r.name)
            out.append(r)
        return out

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = self._public_client_metadata(super().get_routes(mcp_path))
        routes.append(Route("/local-login", self.login_get, methods=["GET"]))
        routes.append(Route("/local-login", self.login_post, methods=["POST"]))
        return routes

    # ------------------------------------------------------------ codes and tokens
    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        d = await self.store.get(_h(authorization_code), collection="codes")
        if not d or d["client_id"] != client.client_id or d["expires_at"] < time.time():
            return None
        return AuthorizationCode(code=authorization_code, client_id=d["client_id"], redirect_uri=d["redirect_uri"],
                                 redirect_uri_provided_explicitly=d["redirect_uri_provided_explicitly"],
                                 scopes=d["scopes"], expires_at=d["expires_at"], code_challenge=d["code_challenge"],
                                 resource=d.get("resource"))

    async def _issue(self, client_id: str, scopes: list[str], resource: str | None,
                     login: str | None = None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = int(time.time())
        login = login or self.user
        await self.store.put(_h(access), {"client_id": client_id, "scopes": scopes, "expires_at": now + ACCESS_TTL,
                                          "login": login, "resource": resource, "refresh": _h(refresh)},
                             collection="access", ttl=ACCESS_TTL)
        await self.store.put(_h(refresh), {"client_id": client_id, "scopes": scopes, "login": login,
                                           "expires_at": now + REFRESH_TTL, "resource": resource,
                                           "access": _h(access)}, collection="refresh", ttl=REFRESH_TTL)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL, refresh_token=refresh,
                          scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        key = _h(authorization_code.code)
        d = await self.store.get(key, collection="codes")
        if not d:
            raise TokenError("invalid_grant", "authorization code not found or already used")
        await self.store.delete(key, collection="codes")
        return await self._issue(client.client_id or "", authorization_code.scopes, d.get("resource"), d.get("login"))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        d = await self.store.get(_h(refresh_token), collection="refresh")
        if not d or d["client_id"] != client.client_id or d["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, client_id=d["client_id"], scopes=d["scopes"],
                            expires_at=d["expires_at"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "requested scopes exceed the original grant")
        d = await self.store.get(_h(refresh_token.token), collection="refresh") or {}
        await self._revoke_pair(refresh=_h(refresh_token.token))  # rotation: old pair dies
        return await self._issue(client.client_id or "", scopes or refresh_token.scopes, d.get("resource"),
                                 d.get("login"))

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        d = await self.store.get(_h(token), collection="access")
        if not d or d["expires_at"] < time.time():
            return None
        return AccessToken(token=token, client_id=d["client_id"], scopes=d["scopes"], expires_at=d["expires_at"],
                           resource=d.get("resource"), claims={"login": d["login"], "sub": d["login"]})

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        return await self.load_access_token(token)

    async def _revoke_pair(self, access: str | None = None, refresh: str | None = None) -> None:
        if access:
            d = await self.store.get(access, collection="access")
            refresh = refresh or (d or {}).get("refresh")
            await self.store.delete(access, collection="access")
        if refresh:
            d = await self.store.get(refresh, collection="refresh")
            if d and d.get("access"):
                await self.store.delete(d["access"], collection="access")
            await self.store.delete(refresh, collection="refresh")

    async def revoke_token(self, token) -> None:  # AccessToken | RefreshToken
        if isinstance(token, RefreshToken):
            await self._revoke_pair(refresh=_h(token.token))
        else:
            await self._revoke_pair(access=_h(token.token))


def main(argv: list[str] | None = None) -> int:
    import getpass
    args = sys.argv[1:] if argv is None else argv
    if args[:1] != ["hash"]:
        print("usage: python -m hub_gateway.local_auth hash   (asks the password twice, prints HUB_LOCAL_PASSWORD_HASH)")
        return 2
    pw = getpass.getpass("password: ")
    if len(pw) < 12:
        print("use at least 12 characters", file=sys.stderr)
        return 1
    if getpass.getpass("again: ") != pw:
        print("passwords differ", file=sys.stderr)
        return 1
    print(hash_password(pw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
