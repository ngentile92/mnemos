"""hub-router MCP server: one URL, scoped per app (docs/design/single-connector.md)."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import SWITCH, RouterOAuthProvider, granted_contexts
from .config import RouterSettings

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


def build_router(s: RouterSettings, *, provider: RouterOAuthProvider | None = None, http=None) -> FastMCP:
    provider = provider or RouterOAuthProvider(
        base_url=s.public_url, contexts=s.contexts, store=build_store(s), user=s.local_user,
        password_hash=s.local_password_hash, idps=s.idps())
    mcp = FastMCP("mnemos", auth=provider, instructions=(
        "Mnemos: memory, skills and credentials, split by context. Every tool takes a `context` argument; you can "
        "only use the contexts this app was granted (see hub_whoami)."))
    mcp.provider = provider  # type: ignore[attr-defined]

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "router": True})

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def hub_whoami() -> dict[str, Any]:
        """Who you are signed in as and which contexts this app may use."""
        g = current_grant()
        return {"login": g["login"], "contexts": g["contexts"], "can_switch": g["switch"]}

    return mcp


def create_app():
    logging.basicConfig(level=os.environ.get("HUB_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from ..internal import StripInternalHeaders

    return StripInternalHeaders(build_router(RouterSettings.from_env()).http_app(path="/mcp"))


def main() -> None:
    import uvicorn

    s = RouterSettings.from_env()
    uvicorn.run("hub_gateway.router.server:create_app", factory=True, host=s.host, port=s.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
