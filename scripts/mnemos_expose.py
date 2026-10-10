#!/usr/bin/env python3
"""Publish every hub in one step, idempotently. Dry run by default.

    scripts/mnemos expose                 # plan: what is published, what is missing (changes nothing)
    scripts/mnemos expose --apply         # do only the missing steps, then verify from public DNS
    scripts/mnemos expose --check         # exit 1 if anything is missing (for scripts/CI)
    scripts/mnemos expose --dashboard     # also publish the dashboard on the tailnet (:8444)

What "exposed" means:
  - public (Funnel): each context's `ts-<ctx>` sidecar + `gateway-<ctx>` are running; the sidecar's serve.json
    turns Funnel on for that hostname only. Check: public DNS (DoH 1.1.1.1/8.8.8.8) resolves
    hub-<ctx>.<tailnet>.ts.net and POST /mcp answers 401 + WWW-Authenticate (OAuth required).
  - tailnet only (`tailscale serve` on the host): Vaultwarden :443 → 127.0.0.1:8081, Infisical :8443 →
    127.0.0.1:8082 and, with --dashboard, :8444 → 127.0.0.1:8790.

Safety: it never runs `tailscale funnel`, never removes or rewrites an existing serve entry (a port that already
points elsewhere is reported as a conflict and left alone), never stops containers, and refuses to touch the host
serve config if Funnel is enabled on the host itself (only the hub-* sidecars may be public).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("MNEMOS_DIR") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "gateway" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

HOST_SERVE = {443: ("vaultwarden", 8081), 8443: ("infisical", 8082)}
DASHBOARD = {8444: ("dashboard", 8790)}
TS_CANDIDATES = ("tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
                 "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


# ---------------------------------------------------------------- pure planning (tested)

def host_actions(status: dict[str, Any], dns_name: str, wanted: dict[int, tuple[str, int]]) -> list[dict[str, Any]]:
    """Compares `tailscale serve status --json` with what we want on the host."""
    web = status.get("Web") or {}
    funnel = {k for k, v in (status.get("AllowFunnel") or {}).items() if v}
    out = []
    for port, (what, local) in sorted(wanted.items()):
        target = f"http://127.0.0.1:{local}"
        key = f"{dns_name}:{port}"
        handlers = (web.get(key) or {}).get("Handlers") or {}
        current = (handlers.get("/") or {}).get("Proxy")
        item = {"kind": "host", "what": what, "port": port, "target": target, "current": current}
        if key in funnel:
            item.update(state="danger", note="Funnel is ON for this host port: it is public. Turn it off by hand: "
                                             f"tailscale funnel --https={port} off")
        elif current == target:
            item.update(state="ok")
        elif current:
            item.update(state="conflict", note=f"port {port} already proxies to {current}; left untouched")
        else:
            item.update(state="missing", cmd=["serve", "--bg", f"--https={port}", target])
        out.append(item)
    return out


def hub_actions(contexts: list, running: set[str], public: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """contexts: names, or (key, label, services) targets (hub_watchdog.targets, includes the router);
    running: compose services in state running; public: key → {"dns": bool, "mcp": bool}."""
    out = []
    for t in contexts:
        c, label, svcs = t if isinstance(t, tuple) else (t, f"hub-{t}", [f"ts-{t}", f"gateway-{t}"])
        down = [s for s in svcs if s not in running]
        p = public.get(c) or {}
        item = {"kind": "hub", "what": label, "services": svcs, "down": down,
                "dns": p.get("dns"), "mcp": p.get("mcp")}
        if down:
            item.update(state="missing", cmd=["up", "-d", *svcs])
        elif p and not (p.get("dns") and p.get("mcp")):
            # containers up but not reachable from the internet: recreate only this pair (same as the watchdog)
            item.update(state="unreachable", cmd=["up", "-d", "--force-recreate", *svcs])
        else:
            item.update(state="ok")
        out.append(item)
    return out


def summary(items: list[dict[str, Any]]) -> bool:
    return all(i["state"] == "ok" for i in items)


def describe(i: dict[str, Any]) -> str:
    if i["kind"] == "host":
        base = f"{i['what']:12} tailnet :{i['port']} → {i['target']}"
    else:
        extra = "" if i["dns"] is None else f" (public DNS {'ok' if i['dns'] else 'NO'}, /mcp→401 {'ok' if i['mcp'] else 'NO'})"
        base = f"{i['what']:12} Funnel via {i['services'][0]}{extra}"
    tail = {"ok": "ok", "missing": "MISSING", "unreachable": "UNREACHABLE", "conflict": "CONFLICT",
            "danger": "DANGER"}[i["state"]]
    return f"[{tail:11}] {base}" + (f" — {i['note']}" if i.get("note") else "")


# ---------------------------------------------------------------- I/O

def ts_bin() -> str | None:
    for c in TS_CANDIDATES:
        p = shutil.which(c)
        if p:
            return p
    return None


def ts_json(ts: str, *args: str) -> dict[str, Any]:
    r = subprocess.run([ts, *args, "--json"], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise SystemExit(f"tailscale {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout or "{}")


def compose_running() -> set[str]:
    r = subprocess.run(["docker", "compose", "ps", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
                       timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"docker compose ps failed: {r.stderr.strip()[:200]}")
    rows = []
    text = r.stdout.strip()
    if text.startswith("["):
        rows = json.loads(text)
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return {row.get("Service") for row in rows if row.get("State") == "running"}


def public_checks(contexts: list, tailnet: str) -> dict[str, dict[str, Any]]:
    from hub_watchdog import check_mcp, resolve_public_dns
    out = {}
    for t in contexts:
        c, label = (t[0], t[1]) if isinstance(t, tuple) else (t, f"hub-{t}")
        host = f"{label}.{tailnet}.ts.net"
        out[c] = {"dns": resolve_public_dns(host)["ok"], "mcp": check_mcp(f"https://{host}")["ok"]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="run the missing steps (default: plan only)")
    g.add_argument("--check", action="store_true", help="exit 1 if anything is not ok")
    ap.add_argument("--dashboard", action="store_true", help="also publish the dashboard on the tailnet (:8444)")
    ap.add_argument("--no-public-check", action="store_true", help="skip DoH + /mcp checks (offline)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from hub_gateway.contexts import CONTEXTS
    from hub_watchdog import read_env, targets
    env = read_env(ROOT / ".env")
    tailnet = env.get("TS_TAILNET", "")
    contexts = targets(list(CONTEXTS), env)  # + the router (mnemos.<tailnet>) when COMPOSE_PROFILES has `router`
    ts = ts_bin()
    if not ts:
        raise SystemExit("tailscale CLI not found (see docs/SETUP.md, step 3)")
    self_dns = (ts_json(ts, "status").get("Self") or {}).get("DNSName", "").rstrip(".")
    wanted = dict(HOST_SERVE)
    serve = ts_json(ts, "serve", "status")
    if args.dashboard or f"{self_dns}:8444" in (serve.get("Web") or {}):
        from hub_watchdog import dashboard_url
        wanted[8444] = ("dashboard", int(dashboard_url(env).rsplit(":", 1)[1]))

    def plan() -> list[dict[str, Any]]:
        pub = {} if args.no_public_check or not tailnet else public_checks(contexts, tailnet)
        return host_actions(ts_json(ts, "serve", "status"), self_dns, wanted) + hub_actions(
            contexts, compose_running(), pub)

    items = plan()
    if args.apply:
        if any(i["state"] == "danger" for i in items):
            for i in items:
                print(describe(i))
            raise SystemExit("Funnel is on for the host: fix that first (nothing changed)")
        todo = [i for i in items if i["state"] in ("missing", "unreachable")]
        for i in todo:
            if i["kind"] == "host":
                cmd = [ts, *i["cmd"]]
                subprocess.run(cmd, check=True, timeout=60, capture_output=True)
            else:
                cmd = ["docker", "compose", *i["cmd"]]
                subprocess.run(cmd, cwd=ROOT, check=True, timeout=600)
            print("ran:", " ".join(cmd[1:] if i["kind"] == "host" else cmd))
        if not todo:
            print("nothing to do")
        else:
            items = plan()
    if args.json:
        print(json.dumps(items, indent=1))
    else:
        for i in items:
            print(describe(i))
        if not args.apply and any(i["state"] in ("missing", "unreachable") for i in items):
            print("\nplan only: re-run with --apply to do the missing steps")
    ok = summary(items)
    return 0 if ok or not (args.check or args.apply) else 1


if __name__ == "__main__":
    sys.exit(main())
