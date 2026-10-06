"""Unit tests for scripts/hub_watchdog.py (sin Docker ni red real)."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hub_watchdog.py"

spec = importlib.util.spec_from_file_location("hub_watchdog", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def test_parse_doh_answers_a_records():
    payload = {
        "Status": 0,
        "Answer": [
            {"type": 1, "data": "1.2.3.4"},
            {"type": 5, "data": "cname.example"},
            {"type": 1, "data": "5.6.7.8"},
        ],
    }
    assert wd.parse_doh_answers(payload) == ["1.2.3.4", "5.6.7.8"]


def test_parse_doh_nxdomain():
    assert wd.parse_doh_answers({"Status": 3, "Answer": []}) == []
    assert wd.parse_doh_answers({"Status": 0}) == []


def test_in_cooldown_and_mark(tmp_path, monkeypatch):
    state: dict = {"recreates": {}}
    assert wd.in_cooldown(state, "personal", time.time()) is False
    wd.mark_recreate(state, "personal", "dns", True)
    assert wd.in_cooldown(state, "personal", time.time(), cooldown_s=1800) is True
    assert wd.in_cooldown(state, "personal", time.time() + 1900, cooldown_s=1800) is False
    assert state["recreates"]["personal"]["reason"] == "dns"


def test_hub_ok_requires_dns_and_mcp():
    assert wd.hub_ok({"ok": True}, {"ok": True}) is True
    assert wd.hub_ok({"ok": False}, {"ok": True}) is False
    assert wd.hub_ok({"ok": True}, {"ok": False}) is False


def test_run_dry_run_without_tailnet(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(wd, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(wd, "LOG_PATH", tmp_path / "log.txt")
    monkeypatch.setattr(wd, "LOG_DIR", tmp_path)
    monkeypatch.delenv("TS_TAILNET", raising=False)
    (tmp_path / ".env").write_text("# empty\n")
    status = wd.run(dry_run=True, notify=False, wait_after_recreate=0)
    assert status["ok"] is False and status["error"] == "sin TS_TAILNET"
    assert json.loads((tmp_path / "status.json").read_text())["error"] == "sin TS_TAILNET"


def test_run_dry_run_all_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(wd, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(wd, "LOG_PATH", tmp_path / "log.txt")
    monkeypatch.setattr(wd, "LOG_DIR", tmp_path)
    (tmp_path / ".env").write_text("TS_TAILNET=tailtest\n")

    def fake_dns(name, timeout=8.0):
        return {"name": name, "ok": True, "addrs": ["9.9.9.9"], "resolvers": {"cloudflare": {"ok": True}}}

    def fake_mcp(base, timeout=15.0):
        return {"url": base + "/mcp", "ok": True, "http": 401}

    monkeypatch.setattr(wd, "resolve_public_dns", fake_dns)
    monkeypatch.setattr(wd, "check_mcp", fake_mcp)
    monkeypatch.setattr(wd, "check_cognee", lambda: {"ok": True, "http": 200})
    monkeypatch.setattr(wd, "check_dashboard", lambda: {"ok": True, "http": 200})
    called = []
    monkeypatch.setattr(wd, "recreate_hub", lambda ctx, dry_run: called.append(ctx) or {"ok": True, "dry_run": dry_run})

    status = wd.run(dry_run=True, notify=False, wait_after_recreate=0)
    assert status["ok"] is True
    assert called == []
    assert all(status["hubs"][c]["ok"] for c in wd.CONTEXTS)
    saved = json.loads((tmp_path / "status.json").read_text())
    assert saved["hubs"]["personal"]["dns_ok"] is True


def test_run_recreates_on_dns_fail_respects_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(wd, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(wd, "LOG_PATH", tmp_path / "log.txt")
    monkeypatch.setattr(wd, "LOG_DIR", tmp_path)
    (tmp_path / ".env").write_text("TS_TAILNET=tailtest\n")

    def fake_dns(name, timeout=8.0):
        ctx = "personal" if "personal" in name else ("side" if "side" in name else "work")
        ok = ctx != "personal"
        return {"name": name, "ok": ok, "addrs": ["1.1.1.1"] if ok else [], "resolvers": {}}

    monkeypatch.setattr(wd, "resolve_public_dns", fake_dns)
    monkeypatch.setattr(wd, "check_mcp", lambda base, timeout=15.0: {"url": base + "/mcp", "ok": True, "http": 401})
    monkeypatch.setattr(wd, "check_cognee", lambda: {"ok": True, "http": 200})
    monkeypatch.setattr(wd, "check_dashboard", lambda: {"ok": True, "http": 200})
    recreates = []
    monkeypatch.setattr(
        wd, "recreate_hub",
        lambda ctx, dry_run: recreates.append(ctx) or {"ok": True, "dry_run": dry_run, "services": [f"ts-{ctx}"]},
    )

    s1 = wd.run(dry_run=True, notify=False, wait_after_recreate=0, cooldown_s=1800)
    assert "personal" in recreates and s1["hubs"]["personal"]["recreated"] is True

    # Simulate cooldown state as if a real recreate happened
    state = {"recreates": {"personal": {"at": "x", "at_epoch": time.time(), "reason": "dns", "ok": True}}, "prev": {}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    recreates.clear()
    s2 = wd.run(dry_run=True, notify=False, wait_after_recreate=0, cooldown_s=1800)
    assert recreates == [] and s2["hubs"]["personal"]["cooldown"] is True


def test_run_does_not_recreate_when_offline(tmp_path, monkeypatch):
    for name, val in (("ROOT", tmp_path), ("STATUS_PATH", tmp_path / "status.json"), ("STATE_PATH", tmp_path / "state.json"),
                      ("LOG_PATH", tmp_path / "log.txt"), ("LOG_DIR", tmp_path)):
        monkeypatch.setattr(wd, name, val)
    monkeypatch.setenv("TS_TAILNET", "tail123")
    offline = {"ok": False, "addrs": [], "resolvers": {"cloudflare": {"ok": False, "error": "ConnectError"},
                                                       "google": {"ok": False, "error": "ConnectError"}}}
    monkeypatch.setattr(wd, "resolve_public_dns", lambda name, timeout=8.0: dict(offline, name=name))
    monkeypatch.setattr(wd, "check_mcp", lambda base, timeout=15.0: {"ok": False, "error": "ConnectError"})
    monkeypatch.setattr(wd, "check_cognee", lambda: {"ok": True, "http": 200})
    monkeypatch.setattr(wd, "check_dashboard", lambda: {"ok": True, "http": 200})
    called = []
    monkeypatch.setattr(wd, "recreate_hub", lambda ctx, dry_run: called.append(ctx) or {"ok": True})
    st = wd.run(dry_run=False, notify=False, wait_after_recreate=0)
    assert called == [] and st["ok"] is False
    assert all(h.get("no_network") for h in st["hubs"].values())
