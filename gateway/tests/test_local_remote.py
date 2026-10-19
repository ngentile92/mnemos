"""Reach from anywhere: Tailscale Funnel (faked: tests never run the real CLI) + OAuth on the public Host only."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from hub_gateway import local
from hub_gateway.local_apps import Apps
from hub_gateway.local_remote import Remote, Tailscale, client_key

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("oauth_probe", ROOT / "scripts" / "oauth_probe.py")
probe_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_mod)

HOST = "mac.tail1234.ts.net"
PW = "a long enough password"
W = {"X-Mnemos-Write": "1"}
STATUS = {"BackendState": "Running", "CertDomains": [HOST], "Self": {"DNSName": HOST + ".", "CapMap": {"funnel": None}}}


class FakeTS:
    """Records every CLI call; serves `status` and `funnel status` from in-memory state."""

    def __init__(self, status=STATUS, cfg=None, enable_out=(0, "")):
        self.calls: list[list[str]] = []
        self.status, self.cfg, self.enable_out = status, cfg or {}, enable_out

    def __call__(self, argv, timeout):
        args = argv[1:]
        self.calls.append(args)
        if args == ["status", "--json"]:
            return (0, json.dumps(self.status)) if self.status is not None else (1, "Tailscale is stopped.")
        if args == ["funnel", "status", "--json"]:
            return 0, json.dumps(self.cfg)
        if args[:2] == ["funnel", "--bg"]:
            code, out = self.enable_out
            if code == 0:
                port = next(a for a in args if a.startswith("--https="))[8:]
                self.cfg.setdefault("TCP", {})[port] = {"HTTPS": True}
                self.cfg.setdefault("Web", {})[f"{HOST}:{port}"] = {"Handlers": {"/": {"Proxy": args[-1]}}}
            return code, out
        if args[0] == "funnel" and args[-1] == "off":
            port = args[1][8:]
            self.cfg.get("TCP", {}).pop(port, None)
            self.cfg.get("Web", {}).pop(f"{HOST}:{port}", None)
            return 0, ""
        return 1, "unexpected"


def ts(fake):
    return Tailscale(runner=fake, binary="/fake/tailscale")


def test_status_and_port_choice_leave_other_funnels_alone():
    st = ts(FakeTS()).status()
    assert st == {"installed": True, "running": True, "dns_name": HOST, "https": True, "funnel_allowed": True,
                  "login_url": ""}
    other = {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}}
    assert Tailscale.pick_port(other, HOST, 8765) == 8443  # 443 serves something else: never touch it
    mine = {"TCP": {"10000": {}}, "Web": {f"{HOST}:10000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}
    assert Tailscale.pick_port(mine, HOST, 8765) == 10000  # already ours: reuse
    full = {"TCP": {"443": {}, "8443": {}, "10000": {}}}
    assert Tailscale.pick_port(full, HOST, 8765) is None


def test_enable_reports_admin_link_and_disable_is_scoped():
    fake = FakeTS(enable_out=(None, "Funnel is not enabled on your tailnet.\nTo enable, visit:\n\n"
                                     "         https://login.tailscale.com/f/funnel?node=abc\n"))
    r = ts(fake).enable(443, 8765)
    assert r["ok"] is False and r["needs"] == "tailnet-admin" and r["url"] == "https://login.tailscale.com/f/funnel?node=abc"
    assert fake.calls[-1] == ["funnel", "--bg", "--yes", "--https=443", "http://127.0.0.1:8765"]
    assert ts(fake).disable(8443)["ok"] and fake.calls[-1] == ["funnel", "--https=8443", "off"]
    old = FakeTS(enable_out=(1, "flag provided but not defined: -yes"))
    ts(old).enable(443, 8765)
    assert old.calls[-1] == ["funnel", "--bg", "--https=443", "http://127.0.0.1:8765"]  # older CLI: retry without


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.setenv("MNEMOS_HYGIENE", "0")
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


async def test_off_by_default_needs_password_and_tailscale(home):
    fake = FakeTS(status=None)
    r = Remote(home, 8765, lambda **kw: None, lambda: [("personal", "Personal")], tailscale=ts(fake), keep_awake_cmd=[])
    st = await r.status()
    assert st["enabled"] is False and st["has_password"] is False and not r.matches(HOST.encode())
    assert (await r.enable())["needs"] == "password"
    assert (await r.enable("short"))["needs"] == "password"
    out = await r.enable(PW)
    assert out["needs"] == "tailscale-login" and r.auth() and oct((home / "auth.json").stat().st_mode)[-3:] == "600"
    nots = Remote(home, 8765, lambda **kw: None, lambda: [], tailscale=Tailscale(runner=fake, binary=""),
                  keep_awake_cmd=[])
    nots.ts.binary = lambda: None
    assert (await nots.enable())["needs"] == "tailscale"
    assert not [c for c in fake.calls if c[:2] == ["funnel", "--bg"]]  # nothing exposed


async def test_public_host_reaches_only_oauth_and_mcp(home):
    fake = FakeTS(cfg={"TCP": {"443": {}}, "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}})
    gate = local.http_app(home, 8765, remote_opts={"tailscale": ts(fake), "keep_awake_cmd": []})
    app = gate.app
    pub_base = f"https://{HOST}:8443"
    async with app.router.lifespan_context(app):
        tr = httpx.ASGITransport(app=gate)
        async with httpx.AsyncClient(transport=tr, base_url="http://127.0.0.1:8765") as lc, \
                httpx.AsyncClient(transport=tr, base_url=pub_base) as pc:
            assert (await pc.get("/.well-known/oauth-authorization-server")).status_code == 403  # off: refused
            await lc.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W)
            assert (await lc.get("/api/remote")).json()["enabled"] is False
            assert (await lc.post("/api/remote", json={"on": True, "password": PW})).status_code == 403  # no W header
            on = (await lc.post("/api/remote", json={"on": True, "password": PW}, headers=W)).json()
            assert on["ok"] and on["enabled"] and on["url"] == f"{pub_base}/mcp", on
            assert ["funnel", "--bg", "--yes", "--https=8443", "http://127.0.0.1:8765"] in fake.calls
            # the public name only has the OAuth endpoints + /mcp; the local app, API and token-gated paths are gone
            for path in ("/", "/app", "/api/session", "/health", "/personal/mcp", "/ui/local.js"):
                assert (await pc.get(path)).status_code == 404, path
            assert (await pc.post("/mcp", json={}, headers={"authorization": "Bearer "
                                                              + (home / "token").read_text().strip()})).status_code == 401
            out = await probe_mod.probe(pc, pub_base, "me", PW, contexts=["personal"],
                                        calls=[("hub_whoami", {}), ("memory_save", {"text": "Ana prefiere té", "context": "work"}),
                                               ("memory_save", {"text": "Ana prefiere té verde"})])
            assert probe_mod.check(out) == [], out
            who, denied, saved = out["calls"]
            assert who["ok"] and '"app_id":"remote-' in who["text"].replace(" ", "")
            assert not denied["ok"] and "may not use the space" in denied["text"]
            assert saved["ok"]
            apps = (await lc.get("/api/apps")).json()["apps"]
            rem = next(a for a in apps if a["via"] == "remote")
            assert rem["label"] == "oauth-probe" and rem["key"] == client_key(rem["oauth_client"])
            ev = (await lc.get(f"/api/activity?app={rem['key']}")).json()["events"]
            assert {e["kind"] for e in ev} >= {"write", "denied"}
            # Remove access: the grant goes, so the token stops at the door
            prov = app.state.remote.provider
            assert len(await prov.list_grants()) == 1
            await lc.post("/api/apps/update", json={"key": rem["key"], "revoked": True}, headers=W)
            assert await prov.list_grants() == []
            # turning it off removes only our Funnel entry and refuses the public name again
            off = (await lc.post("/api/remote", json={"on": False}, headers=W)).json()
            assert off["enabled"] is False and ["funnel", "--https=8443", "off"] in fake.calls
            assert "443" in fake.cfg["TCP"]  # the other service is untouched
            assert (await pc.get("/.well-known/oauth-authorization-server")).status_code == 403
    assert Apps(home, ["personal", "work"]).get(rem["key"])["revoked"] is True


async def test_keep_awake_follows_the_switch(home, tmp_path):
    fake = FakeTS()
    marker = ["sleep", "30"]
    r = Remote(home, 8765, lambda **kw: local.build_router(home, local.build_all(home, 8765), **kw),
               lambda: [("personal", "Personal"), ("work", "Work")], tailscale=ts(fake), keep_awake_cmd=marker)
    assert (await r.enable(PW))["ok"]
    assert r._awake is not None and r._awake.poll() is None
    await r.set_keep_awake(False)
    assert r._awake is None
    await r.set_keep_awake(True)
    p = r._awake
    await r.disable()
    assert r._awake is None and p.wait(timeout=5) is not None
    assert isinstance(p, subprocess.Popen)
