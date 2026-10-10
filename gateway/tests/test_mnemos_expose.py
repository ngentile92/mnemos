"""scripts/mnemos_expose.py planning: idempotent, never rewrites foreign serve entries, flags host Funnel."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mnemos_expose", ROOT / "scripts" / "mnemos_expose.py")
mx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mx)

DNS = "mac.tail1.ts.net"
LIVE = {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8081"}}},
                f"{DNS}:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8082"}}}}}


def test_host_already_published_is_noop():
    items = mx.host_actions(LIVE, DNS, mx.HOST_SERVE)
    assert [i["state"] for i in items] == ["ok", "ok"] and not any("cmd" in i for i in items)


def test_host_missing_port_gets_serve_bg_never_funnel():
    items = mx.host_actions({}, DNS, {**mx.HOST_SERVE, **mx.DASHBOARD})
    cmds = [i["cmd"] for i in items]
    assert cmds[0] == ["serve", "--bg", "--https=443", "http://127.0.0.1:8081"]
    assert all("funnel" not in c for cmd in cmds for c in cmd)


def test_host_conflict_left_alone():
    st = {"Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}}
    i = mx.host_actions(st, DNS, mx.HOST_SERVE)[0]
    assert i["state"] == "conflict" and "cmd" not in i


def test_host_funnel_is_danger():
    st = {**LIVE, "AllowFunnel": {f"{DNS}:443": True}}
    assert mx.host_actions(st, DNS, mx.HOST_SERVE)[0]["state"] == "danger"


def test_hubs():
    running = {"ts-a", "gateway-a", "ts-b"}
    items = {i["what"]: i for i in mx.hub_actions(["a", "b", "c"], running,
                                                 {"a": {"dns": True, "mcp": True}, "b": {}, "c": {}})}
    assert items["hub-a"]["state"] == "ok"
    assert items["hub-b"]["cmd"] == ["up", "-d", "ts-b", "gateway-b"]
    assert items["hub-c"]["down"] == ["ts-c", "gateway-c"]
    un = mx.hub_actions(["a"], running, {"a": {"dns": False, "mcp": True}})[0]
    assert un["state"] == "unreachable" and "--force-recreate" in un["cmd"]
    assert mx.summary([items["hub-a"]]) and not mx.summary(list(items.values()))
