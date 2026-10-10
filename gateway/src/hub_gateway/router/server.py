"""hub-router MCP server: one URL, scoped per app (docs/design/single-connector.md)."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from collections import OrderedDict
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import SWITCH, RouterOAuthProvider, granted_contexts
from .config import RouterSettings
from .proxy import Forwarder, keep_catalog

log = logging.getLogger("hub.router")


def build_store(s: RouterSettings):
    from cryptography.fernet import Fernet
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    d = os.path.join(s.data_dir, "oauth-router")
    os.makedirs(d, exist_ok=True)
    return FernetEncryptionWrapper(key_value=FileTreeStore(data_directory=d), fernet=Fernet(s.storage_key.encode()))


def current_grant() -> dict[str, Any]:
    from fastmcp.server.dependencies import get_access_token

    tok = get_access_token()
    scopes = list(getattr(tok, "scopes", None) or [])
    claims = getattr(tok, "claims", None) or {}
    return {"login": claims.get("login"), "client_id": getattr(tok, "client_id", None),
            "contexts": granted_contexts(scopes), "switch": SWITCH in scopes}


def client_app() -> str | None:
    try:
        from fastmcp.server.dependencies import get_context

        params = get_context().session.client_params
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
        return str(getattr(info, "name", "") or "") or None
    except Exception:  # noqa: BLE001
        return None


def build_router(s: RouterSettings, *, provider: RouterOAuthProvider | None = None, http=None,
                 forwarder: Forwarder | None = None, discover_tools: bool = True) -> FastMCP:
    provider = provider or RouterOAuthProvider(
        base_url=s.public_url, contexts=s.contexts, store=build_store(s), user=s.local_user,
        password_hash=s.local_password_hash, idps=s.idps())
    forwarder = forwarder or Forwarder(s.backends)

    active: OrderedDict[str, str] = OrderedDict()   # MCP session → context picked with hub_use_context

    def _session() -> str | None:
        try:
            from fastmcp.server.dependencies import get_context

            return get_context().session_id
        except Exception:  # noqa: BLE001
            return None

    def pick(requested: str | None, g: dict) -> str:
        if requested:
            if requested not in g["contexts"]:
                raise ToolError(f"this app was not granted context {requested!r}; granted: {g['contexts']}. "
                                "Reconnect the app to change its contexts.")
            return requested
        sid = _session()
        if g["switch"] and sid and active.get(sid) in g["contexts"]:
            return active[sid]
        if len(g["contexts"]) == 1:
            return g["contexts"][0]
        hint = " or pick one with hub_use_context" if g["switch"] else ""
        raise ToolError(f"pass `context` (one of {g['contexts']}){hint}")

    async def resolve(requested: str | None):
        g = current_grant()
        ctx = pick(requested, g)
        if ctx not in forwarder.backends:
            raise ToolError(f"context {ctx!r} has no gateway configured on the router")
        log.info("route %s → %s (app=%s)", g["login"], ctx, client_app())
        return ctx, g["login"], client_app()

    @asynccontextmanager
    async def lifespan(_server):
        task = asyncio.create_task(keep_catalog(_server, forwarder, s.contexts, resolve,
                                                context_required=False)) if discover_tools else None
        try:
            yield {}
        finally:
            if task:
                task.cancel()

    mcp = FastMCP("mnemos", auth=provider, lifespan=lifespan, instructions=(
        "Mnemos: memory, skills and credentials, split by context. Every tool takes a `context` argument; you can "
        "only use the contexts this app was granted (see hub_whoami)."))
    mcp.provider = provider  # type: ignore[attr-defined]
    mcp.resolve = resolve  # type: ignore[attr-defined]
    mcp.forwarder = forwarder  # type: ignore[attr-defined]

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "router": True})

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def hub_whoami() -> dict[str, Any]:
        """Who you are signed in as and which contexts this app may use."""
        g = current_grant()
        out = {"login": g["login"], "contexts": g["contexts"], "can_switch": g["switch"]}
        sid = _session()
        if g["switch"] and sid in active and active[sid] in g["contexts"]:
            out["active_context"] = active[sid]
        return out

    @mcp.tool(auth=require_scopes(SWITCH),
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def hub_use_context(
        context: Annotated[str, Field(description="One of the contexts this app was granted (hub_whoami)")],
    ) -> dict[str, Any]:
        """Switch the active context for the rest of this chat, so later calls can omit `context`.
        Only available when the app was allowed to switch, and only among its granted contexts."""
        g = current_grant()
        if not g["switch"]:
            raise ToolError("context switching is not enabled for this app")
        if context not in g["contexts"]:
            raise ToolError(f"not granted: {context!r}; granted: {g['contexts']}")
        sid = _session()
        if not sid:
            raise ToolError("no MCP session")
        active[sid] = context
        active.move_to_end(sid)
        while len(active) > 2000:
            active.popitem(last=False)
        log.info("switch %s → %s (app=%s)", g["login"], context, client_app())
        return {"active_context": context}

    return mcp


def admin_app(provider: RouterOAuthProvider, key: str):
    """Grant admin for the dashboard ("Connected apps"): list and revoke. Key-gated, Docker network only,
    published on the host's 127.0.0.1 (compose `ports`). Never exposed by Funnel."""
    import hmac

    from starlette.applications import Starlette
    from starlette.routing import Route

    if len(key or "") < 24:
        raise ValueError("MNEMOS_ROUTER_ADMIN_KEY must be at least 24 characters")

    def ok(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("x-mnemos-admin", "").encode(), key.encode())

    async def grants(request: Request) -> JSONResponse:
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        rows = await provider.list_grants()
        return JSONResponse({"grants": [{k: g.get(k) for k in ("client_id", "client_name", "login", "contexts",
                                                                 "switch", "created")} for g in rows]})

    async def revoke(request: Request) -> JSONResponse:
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            cid = str((await request.json()).get("client_id", ""))
        except ValueError:
            cid = ""
        if not cid:
            return JSONResponse({"error": "client_id required"}, status_code=400)
        return JSONResponse({"revoked": await provider.revoke_grant(cid), "client_id": cid})

    return Starlette(routes=[Route("/admin/grants", grants, methods=["GET"]),
                             Route("/admin/revoke", revoke, methods=["POST"])])


def _public(mcp: FastMCP):
    from ..internal import StripInternalHeaders

    return StripInternalHeaders(mcp.http_app(path="/mcp"))


def create_app():
    logging.basicConfig(level=os.environ.get("HUB_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return _public(build_router(RouterSettings.from_env()))


def main() -> None:
    import uvicorn

    from ..internal import default_bind

    logging.basicConfig(level=os.environ.get("HUB_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = RouterSettings.from_env()
    admin_key = os.environ.get("MNEMOS_ROUTER_ADMIN_KEY") or ""
    if not admin_key:
        uvicorn.run("hub_gateway.router.server:create_app", factory=True, host=s.host, port=s.port,
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1")
        return
    mcp = build_router(s)
    bind = os.environ.get("MNEMOS_ROUTER_ADMIN_BIND_HOST") or default_bind()
    port = int(os.environ.get("MNEMOS_ROUTER_ADMIN_PORT_INTERNAL", "8001"))
    log.info("router admin (grants) on %s:%s", bind, port)
    servers = [uvicorn.Server(uvicorn.Config(_public(mcp), host=s.host, port=s.port, proxy_headers=True,
                                             forwarded_allow_ips="127.0.0.1")),
               uvicorn.Server(uvicorn.Config(admin_app(mcp.provider, admin_key), host=bind, port=port,
                                             lifespan="off"))]

    async def serve() -> None:
        await asyncio.gather(*(x.serve() for x in servers))

    asyncio.run(serve())


if __name__ == "__main__":
    main()
