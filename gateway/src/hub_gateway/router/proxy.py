"""Router tool proxy (single-connector step 3).

Every gateway tool is exposed once, with an extra `context` argument. A call is allowed only when the token
holds `ctx:<context>`; it is then forwarded to that context's gateway over its internal listener
(X-Mnemos-Internal key, never the tailnet), with the caller's login and app as provenance headers. The router
never sees Infisical or Cognee credentials: `secret_http_request` runs inside the gateway as today.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any, Callable

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import ToolAnnotations

from .config import Backend

log = logging.getLogger("hub.router")
LOCAL_TOOLS = {"hub_whoami", "hub_use_context"}          # answered by the router itself
_APP_RE = re.compile(r"[^A-Za-z0-9 ._()/-]")


class Forwarder:
    """Talks to gateways' internal listeners. `factory(backend)` lets tests plug an in-process ASGI client."""

    def __init__(self, backends: dict[str, Backend], factory: Callable | None = None):
        self.backends = backends
        self.factory = factory

    def _client(self, b: Backend, login: str | None = None, app: str | None = None) -> Client:
        headers = {"X-Mnemos-Internal": b.key}
        if login:
            headers["X-Mnemos-Login"] = login
        if app:
            headers["X-Mnemos-App"] = _APP_RE.sub("", app)[:60]
        return Client(StreamableHttpTransport(b.url, headers=headers,
                                              httpx_client_factory=self.factory(b) if self.factory else None), timeout=120)

    async def list_tools(self, context: str):
        async with self._client(self.backends[context]) as c:
            return await c.list_tools()

    async def call(self, context: str, name: str, args: dict, login: str | None, app: str | None):
        async with self._client(self.backends[context], login, app) as c:
            return await c.call_tool_mcp(name, args)


def _with_context(schema: dict, contexts: list[str], required: bool) -> dict:
    s = copy.deepcopy(schema or {"type": "object", "properties": {}})
    s.setdefault("properties", {})
    s["properties"] = {"context": {"type": "string", "enum": contexts,
                                   "description": "Which context to use: one of the contexts this app was granted "
                                                  "(see hub_whoami). May be omitted when the app has a single "
                                                  "context or after hub_use_context."}, **s["properties"]}
    req = [r for r in s.get("required", []) if r != "context"]
    s["required"] = (["context"] if required else []) + req
    return s


class ProxyTool(Tool):
    """A gateway tool re-exposed by the router; `resolve` picks and authorizes the context."""

    model_config = {"arbitrary_types_allowed": True}
    remote: str = ""
    forwarder: Any = None
    resolve: Any = None      # async (requested_context) -> (context, login, app); raises ToolError

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        args = dict(arguments)
        context, login, app = await self.resolve(args.pop("context", None))
        res = await self.forwarder.call(context, self.remote, args, login, app)
        return ToolResult(content=res.content, structured_content=res.structured_content,
                          is_error=bool(res.is_error))


async def discover(forwarder: Forwarder, contexts: list[str]):
    """Tool catalog from the first gateway that answers (all gateways serve the same tools)."""
    last = None
    for c in contexts:
        if c not in forwarder.backends:
            continue
        try:
            return await forwarder.list_tools(c)
        except Exception as exc:  # noqa: BLE001 — try the next gateway
            last = exc
    raise RuntimeError(f"no gateway answered on its internal listener: {last!r}")


def make_tools(remote_tools, forwarder: Forwarder, contexts: list[str], resolve, context_required: bool = True):
    out = []
    for t in remote_tools:
        if t.name in LOCAL_TOOLS:
            continue
        ann = t.annotations if isinstance(t.annotations, ToolAnnotations) else None
        out.append(ProxyTool(name=t.name, description=t.description or "", remote=t.name,
                             parameters=_with_context(getattr(t, "input_schema", None) or t.inputSchema, contexts, context_required),
                             annotations=ann, forwarder=forwarder, resolve=resolve))
    return out


async def keep_catalog(mcp, forwarder: Forwarder, contexts: list[str], resolve, every: float = 600,
                       retry: float = 10, context_required: bool = True) -> None:
    """Background task: register proxy tools as soon as a gateway answers, refresh periodically."""
    have: set[str] = set()
    while True:
        try:
            tools = make_tools(await discover(forwarder, contexts), forwarder, contexts, resolve, context_required)
            for t in tools:
                if t.name in have:
                    try:
                        mcp._local_provider.remove_tool(t.name)
                    except Exception:  # noqa: BLE001
                        pass
                mcp.add_tool(t)
                have.add(t.name)
            log.info("router: %d proxied tools", len(tools))
            await asyncio.sleep(every)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("router: tool discovery failed (%s); retrying", exc)
            await asyncio.sleep(retry)
