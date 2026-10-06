#!/usr/bin/env python3
"""Watchdog DNS/Funnel del AI hub.

Cada corrida (LaunchAgent cada 15 min):
  1. Resuelve hub-{work,personal,side}.<tailnet>.ts.net vía DNS público (1.1.1.1 / 8.8.8.8 DoH).
  2. GET/POST https://hub-*/mcp esperando 401 (OAuth).
  3. Si DNS falta o la conexión falla → `docker compose up -d --force-recreate ts-<ctx> gateway-<ctx>`
     (con cooldown de 30 min por hub para no recrear en loop). Nunca hace Funnel-enable.
  4. Chequea Cognee en 127.0.0.1:8010 y el dashboard en :8787 (reinicia el LaunchAgent si cae).
  5. Escribe ~/Library/Logs/mnemos-watchdog-status.json (el dashboard lo muestra) y loggea a
     ~/Library/Logs/mnemos-watchdog.log. Notificación macOS opcional en fallo/recuperación.

Uso:
  scripts/hub_watchdog.py              # corrida completa
  scripts/hub_watchdog.py --dry-run    # chequea sin recrear ni reiniciar
  scripts/hub_watchdog.py --no-notify  # sin osascript
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
try:
    import httpx
except ModuleNotFoundError:
    if (VENV / "bin" / "python3").exists() and Path(sys.prefix).resolve() != VENV.resolve():
        os.execv(str(VENV / "bin" / "python3"), [str(VENV / "bin" / "python3"), __file__, *sys.argv[1:]])
    raise SystemExit("faltan httpx: usá el .venv del repo")

sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.contexts import CONTEXTS  # noqa: E402  (config/contexts.yaml)


def _label_prefix() -> str:
    """Prefijo de los LaunchAgents: AIHUB_LABEL_PREFIX del entorno o de .env (default io.mnemos)."""
    if os.environ.get("AIHUB_LABEL_PREFIX"):
        return os.environ["AIHUB_LABEL_PREFIX"]
    try:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("AIHUB_LABEL_PREFIX="):
                return line.split("=", 1)[1].split("#")[0].strip() or "io.mnemos"
    except OSError:
        pass
    return "io.mnemos"


LABEL_PREFIX = _label_prefix()

COOLDOWN_S = 30 * 60
LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "mnemos-watchdog.log"
STATUS_PATH = LOG_DIR / "mnemos-watchdog-status.json"
STATE_PATH = Path.home() / "Library" / "Application Support" / "mnemos" / "watchdog-state.json"
LOCK_PATH = Path.home() / "Library" / "Application Support" / "mnemos" / "watchdog.lock"
DASHBOARD_LABEL = f"{LABEL_PREFIX}.dashboard"
COGNEE_URL = "http://127.0.0.1:8010"
DASHBOARD_URL = "http://127.0.0.1:8787"
DOH_ENDPOINTS = (
    ("cloudflare", "https://cloudflare-dns.com/dns-query"),
    ("google", "https://dns.google/resolve"),
)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.split("#", 1)[0].strip().strip("\"'")
        if k:
            out[k] = v
    return out


def tailnet_from_env() -> str | None:
    val = os.environ.get("TS_TAILNET") or read_env(ROOT / ".env").get("TS_TAILNET", "")
    val = (val or "").strip()
    return val if val and not val.startswith("__") else None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_doh_answers(payload: dict) -> list[str]:
    """Extrae registros A (type 1) de una respuesta DoH JSON (RFC 8427 / dns.google)."""
    status = payload.get("Status")
    if status not in (0, "0", None) and status != 0:
        # Status 0 = NOERROR; si falta Answer y Status!=0 → NXDOMAIN/SERVFAIL
        if status != 0:
            return []
    addrs: list[str] = []
    for ans in payload.get("Answer") or []:
        if ans.get("type") == 1 and ans.get("data"):
            addrs.append(str(ans["data"]))
    return addrs


def resolve_public_dns(name: str, timeout: float = 8.0) -> dict[str, Any]:
    """Consulta DoH en Cloudflare (1.1.1.1) y Google (8.8.8.8). OK si alguno devuelve A."""
    results: dict[str, Any] = {"name": name, "ok": False, "addrs": [], "resolvers": {}}
    with httpx.Client(timeout=timeout) as client:
        for label, url in DOH_ENDPOINTS:
            try:
                r = client.get(url, params={"name": name, "type": "A"},
                               headers={"Accept": "application/dns-json"})
                r.raise_for_status()
                addrs = parse_doh_answers(r.json())
                results["resolvers"][label] = {"ok": bool(addrs), "addrs": addrs, "http": r.status_code}
                if addrs:
                    results["ok"] = True
                    for a in addrs:
                        if a not in results["addrs"]:
                            results["addrs"].append(a)
            except Exception as exc:  # noqa: BLE001
                results["resolvers"][label] = {"ok": False, "error": type(exc).__name__}
    return results


def check_mcp(base_url: str, timeout: float = 15.0) -> dict[str, Any]:
    """Espera 401 con WWW-Authenticate (OAuth) en /mcp."""
    url = f"{base_url.rstrip('/')}/mcp"
    out: dict[str, Any] = {"url": url, "ok": False}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as c:
            r = c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"Accept": "application/json, text/event-stream"})
        out["http"] = r.status_code
        out["ok"] = r.status_code == 401 and "www-authenticate" in {k.lower() for k in r.headers}
        if not out["ok"]:
            out["error"] = f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


def in_cooldown(state: dict, ctx: str, now: float, cooldown_s: int = COOLDOWN_S) -> bool:
    last = (state.get("recreates") or {}).get(ctx) or {}
    ts = last.get("at_epoch")
    if not isinstance(ts, (int, float)):
        return False
    return (now - float(ts)) < cooldown_s


def mark_recreate(state: dict, ctx: str, reason: str, ok: bool) -> None:
    state.setdefault("recreates", {})[ctx] = {
        "at": now_iso(),
        "at_epoch": time.time(),
        "reason": reason,
        "ok": ok,
    }


def recreate_hub(ctx: str, dry_run: bool) -> dict[str, Any]:
    """Recrea solo el sidecar+gateway del contexto. Nunca toca Funnel config ni otros servicios."""
    services = [f"ts-{ctx}", f"gateway-{ctx}"]
    cmd = ["docker", "compose", "up", "-d", "--force-recreate", *services]
    out: dict[str, Any] = {"cmd": cmd, "services": services}
    if dry_run:
        out["dry_run"] = True
        out["ok"] = True
        return out
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
        out["returncode"] = r.returncode
        out["ok"] = r.returncode == 0
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().splitlines()
            out["error"] = (err[-1] if err else f"exit {r.returncode}")[:240]
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = type(exc).__name__
    return out


def check_cognee() -> dict[str, Any]:
    out: dict[str, Any] = {"url": f"{COGNEE_URL}/health", "ok": False}
    try:
        r = httpx.get(out["url"], timeout=8)
        out["http"] = r.status_code
        body = r.text[:300].lower()
        if "daphne" in body or "django" in body:
            out["error"] = "no es Cognee"
            return out
        out["ok"] = r.status_code == 200
        if not out["ok"]:
            out["error"] = f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


def check_dashboard() -> dict[str, Any]:
    out: dict[str, Any] = {"url": DASHBOARD_URL, "ok": False}
    try:
        r = httpx.get(f"{DASHBOARD_URL}/api/status", timeout=5)
        out["http"] = r.status_code
        out["ok"] = r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


def restart_dashboard(dry_run: bool) -> dict[str, Any]:
    uid = os.getuid()
    domain = f"gui/{uid}"
    label = DASHBOARD_LABEL
    out: dict[str, Any] = {"label": label}
    if dry_run:
        out["dry_run"] = True
        out["ok"] = True
        return out
    # Preferí dashboard.sh restart (maneja kickstart -k o bootstrap)
    script = ROOT / "scripts" / "dashboard.sh"
    try:
        if script.exists():
            r = subprocess.run([str(script), "restart"], capture_output=True, text=True, timeout=30, check=False)
            out["via"] = "dashboard.sh"
            out["returncode"] = r.returncode
            out["ok"] = r.returncode == 0
            if r.returncode != 0:
                out["error"] = (r.stderr or r.stdout or f"exit {r.returncode}")[:200]
            return out
        r = subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"],
                           capture_output=True, text=True, timeout=15, check=False)
        out["via"] = "launchctl"
        out["returncode"] = r.returncode
        out["ok"] = r.returncode == 0
        if r.returncode != 0:
            out["error"] = (r.stderr or r.stdout or f"exit {r.returncode}")[:200]
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = type(exc).__name__
    return out


def notify_macos(title: str, body: str) -> None:
    # Lightweight: no secrets, no Slack. Fallo silencioso si no hay UI session.
    script = f'display notification {json.dumps(body)[:180]} with title {json.dumps(title)[:60]}'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5, check=False)
    except Exception:  # noqa: BLE001
        pass


def hub_ok(dns: dict, mcp: dict) -> bool:
    return bool(dns.get("ok") and mcp.get("ok"))


def run(dry_run: bool = False, notify: bool = True, cooldown_s: int = COOLDOWN_S,
        wait_after_recreate: float = 25.0) -> dict[str, Any]:
    started = now_iso()
    log(f"== Inicio watchdog{' (dry-run)' if dry_run else ''}")
    tailnet = tailnet_from_env()
    state = load_json(STATE_PATH, {"recreates": {}, "prev": {}})
    prev = state.get("prev") or {}

    status: dict[str, Any] = {
        "started_at": started,
        "finished_at": None,
        "ok": True,
        "dry_run": dry_run,
        "tailnet": tailnet,
        "hubs": {},
        "cognee": {},
        "dashboard": {},
        "actions": [],
        "cooldown_s": cooldown_s,
    }

    if not tailnet:
        status["ok"] = False
        status["error"] = "sin TS_TAILNET"
        log("FAIL sin TS_TAILNET en .env")
        status["finished_at"] = now_iso()
        save_json(STATUS_PATH, status)
        return status

    epoch = time.time()
    for ctx in CONTEXTS:
        name = f"hub-{ctx}.{tailnet}.ts.net"
        base = f"https://{name}"
        dns = resolve_public_dns(name)
        mcp = check_mcp(base) if dns.get("ok") else {"url": f"{base}/mcp", "ok": False, "error": "sin DNS público"}
        # Si DNS OK pero MCP falla, igualmente reintentamos (Funnel caído con registro viejo).
        if dns.get("ok") and not mcp.get("ok"):
            mcp = check_mcp(base)  # ya hecho; keep
        entry: dict[str, Any] = {
            "name": name,
            "dns_ok": bool(dns.get("ok")),
            "dns_addrs": dns.get("addrs") or [],
            "dns_resolvers": dns.get("resolvers") or {},
            "mcp_ok": bool(mcp.get("ok")),
            "mcp_http": mcp.get("http"),
            "mcp_error": mcp.get("error"),
            "ok": hub_ok(dns, mcp),
            "recreated": False,
            "cooldown": False,
            "last_recreate": ((state.get("recreates") or {}).get(ctx) or {}).get("at"),
        }
        log(f"hub-{ctx}: dns={'OK' if entry['dns_ok'] else 'FAIL'} "
            f"mcp={'OK' if entry['mcp_ok'] else 'FAIL'} addrs={entry['dns_addrs'] or '-'}")

        if not entry["ok"]:
            status["ok"] = False
            reason = "dns" if not entry["dns_ok"] else "mcp"
            resolvers = dns.get("resolvers") or {}
            if not entry["dns_ok"] and resolvers and all("error" in v for v in resolvers.values()):
                # Ningún resolver DoH contestó (Mac sin red, recién despierta): no es el Funnel, no recrear.
                entry["no_network"] = True
                log(f"hub-{ctx}: sin red (DoH inalcanzable), no recreo")
                status["actions"].append({"ctx": ctx, "action": "skip_no_network", "reason": reason})
            elif in_cooldown(state, ctx, epoch, cooldown_s):
                entry["cooldown"] = True
                log(f"hub-{ctx}: en cooldown, no recreo (último {entry['last_recreate']})")
                status["actions"].append({"ctx": ctx, "action": "skip_cooldown", "reason": reason})
            else:
                log(f"hub-{ctx}: recreando ts-{ctx} + gateway-{ctx} (motivo={reason})")
                rec = recreate_hub(ctx, dry_run=dry_run)
                entry["recreated"] = True
                entry["recreate"] = {k: rec[k] for k in ("ok", "error", "dry_run", "services") if k in rec}
                status["actions"].append({"ctx": ctx, "action": "recreate", "reason": reason, **entry["recreate"]})
                if not dry_run:
                    mark_recreate(state, ctx, reason, bool(rec.get("ok")))
                    entry["last_recreate"] = (state["recreates"][ctx]).get("at")
                    if rec.get("ok") and wait_after_recreate > 0:
                        time.sleep(wait_after_recreate)
                        dns2 = resolve_public_dns(name)
                        mcp2 = check_mcp(base) if dns2.get("ok") else {"ok": False, "error": "sin DNS público"}
                        entry["dns_ok"] = bool(dns2.get("ok"))
                        entry["dns_addrs"] = dns2.get("addrs") or []
                        entry["mcp_ok"] = bool(mcp2.get("ok"))
                        entry["mcp_http"] = mcp2.get("http")
                        entry["mcp_error"] = mcp2.get("error")
                        entry["ok"] = hub_ok(dns2, mcp2)
                        entry["recheck"] = True
                        log(f"hub-{ctx}: recheck dns={'OK' if entry['dns_ok'] else 'FAIL'} "
                            f"mcp={'OK' if entry['mcp_ok'] else 'FAIL'}")
                        if not entry["ok"]:
                            status["ok"] = False
                        elif status["ok"] is not False:
                            pass
        # Notificaciones por transición
        was_ok = prev.get(ctx)
        if notify and was_ok is not None:
            if was_ok and not entry["ok"]:
                notify_macos("AI Hub · Funnel caído", f"hub-{ctx}: DNS/MCP falló; watchdog actuó")
            elif (not was_ok) and entry["ok"]:
                notify_macos("AI Hub · Funnel OK", f"hub-{ctx}: recuperado")
        prev[ctx] = entry["ok"]
        status["hubs"][ctx] = entry
        # Si tras recheck quedó OK, no ensuciar ok global solo por el fallo inicial
        if not entry["ok"]:
            status["ok"] = False

    # Recalcular ok global limpio
    status["ok"] = all(h.get("ok") for h in status["hubs"].values())

    cog = check_cognee()
    status["cognee"] = cog
    log(f"cognee: {'OK' if cog.get('ok') else 'FAIL'} {cog.get('error') or cog.get('http') or ''}")
    if not cog.get("ok"):
        status["ok"] = False
        if notify and prev.get("cognee") is True:
            notify_macos("AI Hub · Cognee caído", "127.0.0.1:8010 no responde")
    elif notify and prev.get("cognee") is False:
        notify_macos("AI Hub · Cognee OK", "salud recuperada en :8010")
    prev["cognee"] = bool(cog.get("ok"))

    dash = check_dashboard()
    status["dashboard"] = dash
    if not dash.get("ok"):
        log("dashboard: FAIL → reinicio LaunchAgent")
        rst = restart_dashboard(dry_run=dry_run)
        status["dashboard"]["restart"] = rst
        status["actions"].append({"action": "restart_dashboard", **{k: rst[k] for k in ("ok", "error", "dry_run", "via") if k in rst}})
        if not dry_run:
            time.sleep(3)
            dash2 = check_dashboard()
            status["dashboard"].update(dash2)
            status["dashboard"]["recheck"] = True
        if not status["dashboard"].get("ok"):
            status["ok"] = False
        if notify and prev.get("dashboard") is True:
            notify_macos("Mnemos · Dashboard caído", f"reinicié {DASHBOARD_LABEL}")
    else:
        log("dashboard: OK")
        if notify and prev.get("dashboard") is False:
            notify_macos("AI Hub · Dashboard OK", "8787 recuperado")
    prev["dashboard"] = bool(status["dashboard"].get("ok"))

    state["prev"] = prev
    state["last_run"] = started
    if not dry_run:
        save_json(STATE_PATH, state)
    else:
        # En dry-run igual actualizamos status file, no el estado de cooldown real
        pass

    status["finished_at"] = now_iso()
    # last recreate summary for dashboard
    status["last_recreates"] = {
        ctx: (state.get("recreates") or {}).get(ctx) for ctx in CONTEXTS
    }
    save_json(STATUS_PATH, status)
    log(f"== Fin watchdog ok={status['ok']}")
    return status


def acquire_lock() -> Any:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit("otra instancia del watchdog está corriendo")
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="chequear sin recrear ni reiniciar")
    ap.add_argument("--no-notify", action="store_true", help="sin notificación macOS")
    ap.add_argument("--cooldown", type=int, default=COOLDOWN_S, help="segundos de cooldown por hub tras recreate")
    ap.add_argument("--wait-after-recreate", type=float, default=25.0,
                    help="segundos a esperar antes del recheck tras recreate")
    args = ap.parse_args()
    lock = acquire_lock()
    try:
        status = run(dry_run=args.dry_run, notify=not args.no_notify, cooldown_s=args.cooldown,
                     wait_after_recreate=args.wait_after_recreate)
        sys.exit(0 if status.get("ok") else 1)
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
