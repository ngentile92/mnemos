"""Tests de scripts/render_compose.py (compose por contextos de contexts.yaml)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render_compose.py"
spec = importlib.util.spec_from_file_location("render_compose", SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

BASE = yaml.safe_load((ROOT / "compose.yaml").read_text())


def _strip(d):
    return {k: v for k, v in d.items() if not k.startswith("x-")}


def test_default_contexts_render_equals_compose_yaml():
    out = yaml.safe_load(rc.dump(rc.render(BASE, ["work", "personal", "side"])))
    assert out == _strip(BASE)


def test_custom_contexts():
    out = rc.render(BASE, ["acme", "home", "lab-x"])
    s, v = out["services"], out["volumes"]
    assert not any(k.endswith(("-work", "-personal", "-side")) for k in s)
    assert {"ts-acme", "gateway-acme", "ts-lab-x", "gateway-lab-x"} <= set(s)
    assert {"ts_acme", "gw_acme", "ts_home", "gw_home", "cognee_data"} <= set(v)
    g = s["gateway-lab-x"]
    assert g["network_mode"] == "service:ts-lab-x"
    env = g["environment"]
    assert env["HUB_CONTEXT"] == "lab-x"
    assert env["GITHUB_CLIENT_ID"] == "${GH_OAUTH_LAB_X_ID:-}"
    assert env["HUB_PUBLIC_URL"].startswith("https://hub-lab-x.")
    assert "gw_lab-x:/data" in g["volumes"] and "./config:/config:ro" in g["volumes"]
    assert s["ts-acme"]["environment"]["TS_HOSTNAME"] == "hub-acme"
    assert s["ts-acme"]["volumes"][0] == "ts_acme:/var/lib/tailscale"
    # servicios compartidos intactos (y "network" no se reemplaza)
    assert s["cognee"] == BASE["services"]["cognee"] and out["networks"] == BASE["networks"]


def test_cli_write_and_check(tmp_path):
    ctx = tmp_path / "contexts.yaml"
    ctx.write_text("contexts:\n  a: {}\n  b: {}\n")
    out = tmp_path / "compose.generated.yaml"
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), "--contexts", str(ctx), "--out", str(out), *a],  # noqa: E731
                                    capture_output=True, text=True)
    assert run("--check").returncode == 1
    assert run().returncode == 0 and out.read_text().startswith("# GENERADO")
    assert run("--check").returncode == 0
    ctx.write_text("contexts:\n  a: {}\n")
    assert run("--check").returncode == 1


def test_gateways_have_no_extra_hosts():
    """Gateways share the Tailscale sidecar's network (network_mode: service:…): Docker rejects extra_hosts there."""
    import yaml
    from pathlib import Path

    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / "compose.yaml").read_text())
    for name, svc in doc["services"].items():
        if str(svc.get("network_mode", "")).startswith("service:"):
            assert "extra_hosts" not in svc, name
    assert "extra_hosts" not in doc.get("x-gateway", {})


def test_router_gets_one_internal_key_per_context():
    import yaml
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    out = rc.render(base, ["alpha", "beta-two"])
    env = out["services"]["hub-router"]["environment"]
    keys = [k for k in env if k.startswith("HUB_INTERNAL_KEY_")]
    assert keys == ["HUB_INTERNAL_KEY_ALPHA", "HUB_INTERNAL_KEY_BETA_TWO"]
    assert out["services"]["gateway-alpha"]["environment"]["HUB_INTERNAL_KEY"] == "${HUB_INTERNAL_KEY_ALPHA:-}"
    assert out["services"]["hub-router"]["profiles"] == ["router"]
