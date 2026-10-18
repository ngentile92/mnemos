"""Single local connector: one /mcp (and one stdio entry) for every space, with a default space per app."""
from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from hub_gateway import local
from hub_gateway.local_apps import Apps, app_key
from hub_gateway.local_router import instructions


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


def client(router, name="cursor-vscode"):
    return Client(router, client_info=Implementation(name=name, version="1.0"))


def test_app_keys():
    assert app_key("claude-ai") == ("claude-desktop", "Claude Desktop")
    assert app_key("cursor-vscode") == ("cursor", "Cursor")
    assert app_key("Visual Studio Code") == ("vscode", "VS Code")
    assert app_key("My Tool!") == ("my-tool", "My Tool!")
    assert app_key("") == ("unknown", "Unknown app")


async def test_one_connector_default_space_and_override(home):
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers)
    names = {t.name for t in await router.list_tools()}
    assert {"memory_save", "memory_search", "hub_whoami", "hub_use_context"} <= names
    assert not names & {"secrets_list", "secret_http_request"}
    save = next(t for t in await router.list_tools() if t.name == "memory_save")
    assert save.parameters["properties"]["context"]["enum"] == list(servers)
    assert "context" not in save.parameters.get("required", [])
    async with client(router) as c:
        who = data(await c.call_tool("hub_whoami", {}))
        assert who["app"] == "Cursor" and who["default_space"] == list(servers)[0]
        first, other = list(servers)[0], list(servers)[1]
        await c.call_tool("memory_save", {"text": "Ana usa pnpm en todos sus repos"})
        await c.call_tool("memory_save", {"text": "Ana tiene dos gatos, Mia y Leo", "context": other})
    async with Client(servers[first]) as a, Client(servers[other]) as b:
        ta = [i["text"] for i in data(await a.call_tool("memory_list", {}))["items"]]
        tb = data(await b.call_tool("memory_list", {}))["items"]
    assert any("pnpm" in t for t in ta) and not any("gatos" in t for t in ta)
    assert any("gatos" in i["text"] for i in tb)
    assert next(i for i in tb if "gatos" in i["text"])["source_app"].startswith("Cursor")  # provenance kept
    rec = Apps(home, list(servers)).get("cursor")
    assert rec["label"] == "Cursor" and rec["via"] == "local"


async def test_per_app_default_and_allowed_spaces(home):
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers)
    first, other = list(servers)[0], list(servers)[1]
    async with client(router, "claude-ai") as c:
        await c.call_tool("hub_whoami", {})
    apps = Apps(home, list(servers))
    apps.update("claude-desktop", default=other, spaces=[other])
    async with client(router, "claude-ai") as c:
        who = data(await c.call_tool("hub_whoami", {}))
        assert who["default_space"] == other
        assert [s["name"] for s in who["spaces"] if s["allowed"]] == [other]
        await c.call_tool("memory_save", {"text": "Reunión con Beta el jueves a las 10"})
        with pytest.raises(ToolError, match="may not use"):
            await c.call_tool("memory_search", {"query": "pnpm", "context": first})
        with pytest.raises(ToolError, match="may not use"):
            await c.call_tool("hub_use_context", {"context": first})
    async with Client(servers[other]) as b:
        assert any("Beta" in i["text"] for i in data(await b.call_tool("memory_list", {}))["items"])
    # another app is unaffected
    async with client(router, "codex-mcp-client") as c:
        assert data(await c.call_tool("hub_whoami", {}))["default_space"] == first
    with pytest.raises(ValueError):
        apps.update("claude-desktop", default="nope")


async def test_switch_space_for_the_chat(home):
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers, via="stdio")  # one client per stdio process
    other = list(servers)[1]
    async with client(router) as c:
        assert data(await c.call_tool("hub_use_context", {"context": other}))["active_space"] == other
        await c.call_tool("memory_save", {"text": "El cliente Beta paga a 60 días"})
        assert data(await c.call_tool("hub_whoami", {}))["active_space"] == other
    async with Client(servers[other]) as b:
        assert any("60 días" in i["text"] for i in data(await b.call_tool("memory_list", {}))["items"])


def test_instructions_short_and_explicit():
    t = instructions({"personal": "home, family and hobbies", "work": "my job at Acme"})
    assert "ask first" in t and "DATA" in t and "personal (home" in t and len(t) < 1300
    t2 = instructions({"personal": "vida personal", "work": "trabajo"}, "es")
    assert "preguntá antes" in t2 and len(t2) < 1300


def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_http_single_mcp_endpoint(home):
    import uvicorn
    port = _free()
    srv = uvicorn.Server(uvicorn.Config(local.http_app(home, port), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    base, tok = f"http://127.0.0.1:{port}", (home / "token").read_text()
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "cursor-vscode", "version": "1"}}}
    try:
        assert httpx.post(f"{base}/mcp", headers=h, json=init).status_code == 401
        r = httpx.post(f"{base}/mcp", headers={**h, "Authorization": f"Bearer {tok}"}, json=init)
        assert r.status_code == 200 and '"name":"mnemos"' in r.text.replace(" ", "")
        assert "/mcp" in httpx.get(f"{base}/").text
    finally:
        srv.should_exit = True


def test_stdio_without_context_serves_every_space(home):
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-m", "hub_gateway.local", "stdio", "--home", str(home)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def send(m):
        p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-ai", "version": "1"}}})
        assert "serverInfo" in p.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "hub_whoami", "arguments": {}}})
        out = p.stdout.readline()
        assert "Claude Desktop" in out and "default_space" in out
    finally:
        p.kill()
    rec = json.loads((home / "data" / "apps.json").read_text())["apps"]["claude-desktop"]
    assert rec["via"] == "stdio"
