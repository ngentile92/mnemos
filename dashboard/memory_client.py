"""Memory editor backend: calls the gateway's own memory tools through its internal listener.

Every edit goes through the same code as an assistant would use (memory_update / memory_delete /
memory_history / memory_undo, ledger + provenance included), never straight to Cognee. The gateway's internal
listener is published on a random 127.0.0.1 port (compose `ports: 127.0.0.1::8100`) and needs
HUB_INTERNAL_KEY_<CTX> from .env. Only the standard library is used.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
TOOLS = {"list": "memory_list", "history": "memory_history", "update": "memory_update",
         "delete": "memory_delete", "undo": "memory_undo", "pin": "memory_pin",
         "obsolete": "memory_mark_obsolete", "dispute": "memory_dispute", "save": "memory_save"}


class MemoryEditorOff(Exception):
    pass


def suffix(ctx: str) -> str:
    return ctx.upper().replace("-", "_")


def discover_url(root: Path, env: dict[str, str], ctx: str) -> str:
    override = env.get(f"MNEMOS_INTERNAL_URL_{suffix(ctx)}")
    if override:
        return override
    r = subprocess.run(["docker", "compose", "port", f"ts-{ctx}", "8100"], cwd=root, capture_output=True,
                       text=True, timeout=20)
    hostport = (r.stdout.strip().splitlines() or [""])[0]
    if r.returncode != 0 or not hostport.startswith("127.0.0.1:"):
        raise MemoryEditorOff(f"internal listener of {ctx} is not published (run scripts/update.sh)")
    return f"http://{hostport}/mcp"


class GatewayMCP:
    """Minimal streamable-HTTP MCP client (initialize → tools/call), one session per call."""

    def __init__(self, url: str, key: str, timeout: float = 120):
        self.url, self.key, self.timeout = url, key, timeout

    def _post(self, payload: dict, sid: str | None) -> tuple[dict | None, str | None]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "X-Mnemos-Internal": self.key, "X-Mnemos-Login": "dashboard", "X-Mnemos-App": "dashboard"}
        if sid:
            h["mcp-session-id"] = sid
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read().decode()
            sid = r.headers.get("mcp-session-id") or sid
        msg = None
        for line in body.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:])
        if msg is None and body.strip().startswith("{"):
            msg = json.loads(body)
        return msg, sid

    def call(self, tool: str, args: dict) -> dict:
        try:
            _, sid = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "dashboard", "version": "1"}}}, None)
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
            msg, _ = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": tool, "arguments": args}}, sid)
        except urllib.error.HTTPError as e:
            raise ValueError(f"gateway answered HTTP {e.code}") from e
        except OSError as e:
            raise ValueError(f"gateway not reachable ({type(e).__name__})") from e
        res = (msg or {}).get("result") or {}
        if res.get("isError") or not res:
            text = " ".join(b.get("text", "") for b in res.get("content", []) if isinstance(b, dict))
            raise ValueError(text or str((msg or {}).get("error", "tool error"))[:300])
        return res.get("structuredContent") or {}


def run(root: Path, env: dict[str, str], contexts: list[str], action: str, body: dict) -> dict:
    ctx = str(body.get("context", ""))
    if ctx not in contexts:
        raise ValueError(f"unknown context {ctx!r}")
    key = env.get(f"HUB_INTERNAL_KEY_{suffix(ctx)}", "")
    if not key:
        raise MemoryEditorOff(f"HUB_INTERNAL_KEY_{suffix(ctx)} is not set in .env (scripts/init_env.py)")
    tool = TOOLS[action]
    if action == "list":
        args = {"include_shared": bool(body.get("include_shared", True)), "limit": 100}
        q = str(body.get("contains") or "").strip()[:200]
        if q:
            args["contains"] = q
    elif action == "save":
        text = str(body.get("text", "")).strip()
        if not 3 <= len(text) <= 20000:
            raise ValueError("text must be 3-20000 characters")
        args = {"text": text}
        if body.get("project"):
            args["project"] = str(body["project"])[:80]
    elif action == "dispute":
        corr = str(body.get("correction", "")).strip()
        if not 5 <= len(corr) <= 1000:
            raise ValueError("describe the correction in 5-1000 characters")
        args = {"correction": corr}
    else:
        nid = str(body.get("id", ""))
        if not _UUID.match(nid):
            raise ValueError("invalid note id")
        args = {"id": nid}
        if action == "update":
            text = str(body.get("text", ""))
            if not 3 <= len(text) <= 20000:
                raise ValueError("text must be 3-20000 characters")
            args["text"] = text
            args["background"] = True
        elif action == "pin":
            args["pinned"] = bool(body.get("pinned", True))
        elif action == "obsolete":
            args["obsolete"] = bool(body.get("obsolete", True))
            if body.get("reason"):
                args["reason"] = str(body["reason"])[:300]
    return GatewayMCP(discover_url(root, env, ctx), key).call(tool, args)
