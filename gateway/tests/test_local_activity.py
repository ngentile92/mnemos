"""Activity feed per app + pause / remove access, through the single local connector."""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation
from starlette.testclient import TestClient

from hub_gateway import local
from hub_gateway.local_activity import Activity, describe
from hub_gateway.local_apps import Apps

W = {"X-Mnemos-Write": "1"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


def test_describe():
    assert describe("memory_save", {"text": "Ana usa pnpm"}, {"status": "accepted"}) == ("write", "saved “Ana usa pnpm”")
    k, s = describe("memory_search", {"query": "viaje"}, {"results": [1, 2, 3]})
    assert k == "read" and s == "searched “viaje” → 3 notes"
    assert describe("memory_save", {"text": "x" * 200}, None)[1].endswith("…”")


async def test_feed_pause_and_revoke(home):
    servers = local.build_all(home, 8765)
    router = local.build_router(home, servers)
    first = list(servers)[0]
    ci = Implementation(name="cursor-vscode", version="1")
    async with Client(router, client_info=ci) as c:
        await c.call_tool("memory_save", {"text": "Ana usa pnpm en todos sus repos"})
        await c.call_tool("memory_search", {"query": "pnpm", "mode": "keyword"})
    ev = Activity(home).recent()
    assert [e["tool"] for e in ev[:2]] == ["memory_search", "memory_save"]
    assert ev[1]["label"] == "Cursor" and ev[1]["space"] == first and ev[1]["kind"] == "write" and "pnpm" in ev[1]["summary"]
    apps = Apps(home, list(servers))
    apps.update("cursor", paused=True)
    async with Client(router, client_info=ci) as c:
        with pytest.raises(ToolError, match="paused"):
            await c.call_tool("memory_search", {"query": "pnpm", "mode": "keyword"})
        with pytest.raises(ToolError, match="paused"):
            await c.call_tool("hub_whoami", {})
    apps.update("cursor", paused=False, revoked=True)
    async with Client(router, client_info=ci) as c:
        with pytest.raises(ToolError, match="removed"):
            await c.call_tool("memory_save", {"text": "otra cosa importante"})
    ev = Activity(home).recent()
    assert ev[0]["kind"] == "denied" and ev[0]["ok"] == 0
    assert Activity(home).stats()["cursor"]["denied"] >= 2
    apps.update("cursor", revoked=False)
    async with Client(router, client_info=ci) as c:
        await c.call_tool("hub_whoami", {})


def test_apps_and_activity_api(home):
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/apps").status_code == 401
        c.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W)
        assert c.get("/api/apps").json()["apps"] == []
        apps = Apps(home, c.get("/api/session").json()["contexts"])
        apps.touch("cursor", "Cursor")
        r = c.get("/api/apps").json()
        assert r["apps"][0]["key"] == "cursor" and r["apps"][0]["stats"] == {"read": 0, "write": 0, "denied": 0}
        sp = r["spaces"]
        r = c.post("/api/apps/update", json={"key": "cursor", "default": sp[1], "spaces": [sp[1]]}, headers=W).json()
        assert r["apps"][0]["effective_default"] == sp[1] and r["apps"][0]["allowed"] == [sp[1]]
        assert c.post("/api/apps/update", json={"key": "cursor", "default": "nope"}, headers=W).status_code == 400
        assert c.post("/api/apps/update", json={"key": "cursor", "paused": True}).status_code == 403
        assert c.post("/api/apps/update", json={"key": "cursor", "paused": True}, headers=W).json()["apps"][0]["paused"]
        assert c.post("/api/apps/update", json={"default_space": sp[1]}, headers=W).json()["default_space"] == sp[1]
        Activity(home).record("cursor", "Cursor", sp[1], "memory_save", {"text": "hola mundo"}, {}, None)
        ev = c.get("/api/activity?app=cursor").json()["events"]
        assert ev[0]["summary"] == "saved “hola mundo”"
