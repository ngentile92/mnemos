"""Dashboard 'Connected apps' (single-connector step 6): router grant admin + dashboard list/revoke."""
from __future__ import annotations

import asyncio
import importlib.util
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn
from key_value.aio.stores.memory import MemoryStore

from hub_gateway import local_auth
from hub_gateway.router.auth import RouterOAuthProvider
from hub_gateway.router.server import admin_app

ROOT = Path(__file__).resolve().parents[2]
KEY = "a" * 32


def provider():
    return RouterOAuthProvider(base_url="https://m.test", contexts=["work", "personal"], store=MemoryStore(),
                               user="alex", password_hash=local_auth.hash_password("x" * 12, n=2**10))


async def seed(prov):
    for cid, ctxs in (("c1", ["work"]), ("c2", ["work", "personal"])):
        await prov.store.put(cid, {"client_id": cid, "client_name": f"app {cid}", "login": "alex", "contexts": ctxs,
                                   "switch": cid == "c2", "created": 1}, collection="grants")
        await prov._index_grant(cid, add=True)


@pytest.mark.asyncio
async def test_admin_api_key_gated():
    prov = provider()
    await seed(prov)
    app = admin_app(prov, KEY)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        assert (await c.get("/admin/grants")).status_code == 401
        assert (await c.get("/admin/grants", headers={"x-mnemos-admin": "b" * 32})).status_code == 401
        r = await c.get("/admin/grants", headers={"x-mnemos-admin": KEY})
        assert [g["client_id"] for g in r.json()["grants"]] == ["c1", "c2"]
        r = await c.post("/admin/revoke", headers={"x-mnemos-admin": KEY}, json={"client_id": "c1"})
        assert r.json() == {"revoked": True, "client_id": "c1"}
        assert [g["client_id"] for g in (await prov.list_grants())] == ["c2"]
    with pytest.raises(ValueError):
        admin_app(prov, "short")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def stack(tmp_path, monkeypatch):
    prov = provider()
    asyncio.run(seed(prov))
    rport = _free_port()
    server = uvicorn.Server(uvicorn.Config(admin_app(prov, KEY), host="127.0.0.1", port=rport, lifespan="off",
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    (tmp_path / ".env").write_text(f"COMPOSE_PROFILES=router\nMNEMOS_ROUTER_ADMIN_KEY={KEY}\n"
                                   f"MNEMOS_ROUTER_ADMIN_PORT={rport}\n")
    spec = importlib.util.spec_from_file_location("mnemos_dashboard_apps", ROOT / "dashboard" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(None, set(), None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = mod.make_handler(None, {f"127.0.0.1:{port}"}, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, tmp_path, prov
    srv.shutdown()
    server.should_exit = True


def test_dashboard_lists_and_revokes(stack):
    port, root, prov = stack
    base = f"http://127.0.0.1:{port}"
    r = httpx.get(f"{base}/api/router/grants")
    assert r.status_code == 200 and len(r.json()["grants"]) == 2
    assert KEY not in r.text
    # write guard: no custom header / wrong origin → refused
    assert httpx.post(f"{base}/api/router/revoke", json={"client_id": "c1"}).status_code == 403
    r = httpx.post(f"{base}/api/router/revoke", json={"client_id": "c1"},
                   headers={"Origin": base, "X-Mnemos-Write": "1"})
    assert r.status_code == 200 and r.json()["revoked"] is True
    assert [g["client_id"] for g in httpx.get(f"{base}/api/router/grants").json()["grants"]] == ["c2"]


def test_dashboard_router_disabled(stack):
    port, root, _ = stack
    (root / ".env").write_text("TS_TAILNET=x\n")
    r = httpx.get(f"http://127.0.0.1:{port}/api/router/grants")
    assert r.status_code == 409 and "not enabled" in r.json()["error"]
