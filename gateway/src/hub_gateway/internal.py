"""Internal listener: lets the single-connector router (hub-router) call this gateway.

Design: docs/design/single-connector.md, step 1.

- A second HTTP listener (default :8100) serves the same MCP tools WITHOUT OAuth. It is bound to the
  container's Docker network address, never to the tailnet, and never published by Funnel/serve.
- Every request must carry `X-Mnemos-Internal: <HUB_INTERNAL_KEY_<CTX>>` (constant-time compare);
  anything else gets 401 before reaching MCP.
- The router passes who is calling in `X-Mnemos-Login` / `X-Mnemos-App` (provenance only).
- The public listener strips every `x-mnemos-*` header, so a public client can never pose as the router.
  The internal wrapper marks verified requests with a per-process random nonce, which is what the tools check.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import socket

NONCE = secrets.token_hex(16)
VERIFIED = b"x-mnemos-verified"
_PREFIX = b"x-mnemos-"
_SAFE = re.compile(r"[^A-Za-z0-9 ._()@/:-]")


def _clean(value: str | None, limit: int = 60) -> str | None:
    value = _SAFE.sub("", (value or "").strip())[:limit]
    return value or None


def _strip(headers) -> list:
    return [(k, v) for k, v in headers if not k.lower().startswith(_PREFIX)]


class StripInternalHeaders:
    """Wraps the PUBLIC app: drops x-mnemos-* so they can only come from the internal wrapper."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, headers=_strip(scope.get("headers", [])))
        await self.app(scope, receive, send)


class InternalKeyGate:
    """Wraps the INTERNAL app: requires the per-context key, then marks the request as verified."""

    def __init__(self, app, key: str):
        if len(key or "") < 32:
            raise ValueError("HUB_INTERNAL_KEY_<CTX> must be at least 32 characters")
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # lifespan
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        given = next((v for k, v in headers if k.lower() == b"x-mnemos-internal"), b"")
        if scope.get("path") == "/healthz" or not hmac.compare_digest(given, self.key):
            ok = scope.get("path") == "/healthz"
            body = json.dumps({"ok": True} if ok else {"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 200 if ok else 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        keep = [(k, v) for k, v in headers if k.lower() in (b"x-mnemos-login", b"x-mnemos-app")]
        new = _strip(headers) + keep + [(VERIFIED, NONCE.encode())]
        await self.app(dict(scope, headers=new), receive, send)


def caller() -> dict | None:
    """{'login', 'app'} when the current MCP request came through the verified internal listener, else None."""
    try:
        from fastmcp.server.dependencies import get_http_request

        h = get_http_request().headers
    except Exception:  # noqa: BLE001 — no HTTP request (tests in-process, stdio)
        return None
    if not hmac.compare_digest(h.get("x-mnemos-verified", ""), NONCE):
        return None
    return {"login": _clean(h.get("x-mnemos-login")) or "router", "app": _clean(h.get("x-mnemos-app"))}


def default_bind() -> str:
    """The container's Docker-network address (never a tailnet 100.x address)."""
    try:
        addr = socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"
    return "127.0.0.1" if addr.startswith("100.") else addr
