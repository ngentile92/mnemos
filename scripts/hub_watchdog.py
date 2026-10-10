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
  6. Alertas externas opcionales (apagadas por defecto), configurables en .env:
       MNEMOS_ALERT_WEBHOOK_URL   POST JSON genérico (Slack/Discord incoming webhook, etc.)
       MNEMOS_ALERT_NTFY_TOPIC    topic de ntfy (MNEMOS_ALERT_NTFY_SERVER, default https://ntfy.sh)
       MNEMOS_ALERT_COOLDOWN_S    anti-flapping (default 1800): no repite "caído" del mismo componente antes
     1 alerta por caída y 1 por recuperación por componente (estado en watchdog-state.json). El mensaje
     solo lleva el nombre del componente (hub-<ctx>, cognee, dashboard): sin hostnames, tailnet, IPs ni errores.

Uso:
  scripts/hub_watchdog.py              # corrida completa
  scripts/hub_watchdog.py --dry-run    # chequea sin recrear ni reiniciar
  scripts/hub_watchdog.py --no-notify  # sin osascript ni alertas externas
  scripts/hub_watchdog.py --test-alert # manda una alerta de prueba por los canales configurados y sale
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
    """LaunchAgent prefix: MNEMOS_LABEL_PREFIX (or legacy AIHUB_LABEL_PREFIX) from env or .env (default io.mnemos)."""
    for n in ("MNEMOS_LABEL_PREFIX", "AIHUB_LABEL_PREFIX"):
        if os.environ.get(n):
            return os.environ[n]
    try:
        lines = (ROOT / ".env").read_text().splitlines()
    except OSError:
        return "io.mnemos"
    for n in ("MNEMOS_LABEL_PREFIX", "AIHUB_LABEL_PREFIX"):
        for line in lines:
            if line.startswith(f"{n}="):
                return line.split("=", 1)[1].split("#")[0].strip() or "io.mnemos"
    return "io.mnemos"


LABEL_PREFIX = _label_prefix()

def _log_dir() -> Path:
    """macOS: ~/Library/Logs (unchanged). Linux: $MNEMOS_LOG_DIR or $XDG_STATE_HOME/mnemos (~/.local/state/mnemos)."""
    if os.environ.get("MNEMOS_LOG_DIR"):
        return Path(os.environ["MNEMOS_LOG_DIR"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "mnemos"


COOLDOWN_S = 30 * 60
LOG_DIR = _log_dir()
LOG_PATH = LOG_DIR / "mnemos-watchdog.log"
STATUS_PATH = LOG_DIR / "mnemos-watchdog-status.json"
_APP = Path.home() / "Library" / "Application Support" / "mnemos" if sys.platform == "darwin" else LOG_DIR
STATE_PATH = _APP / "watchdog-state.json"
LOCK_PATH = _APP / "watchdog.lock"
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


ROUTER_KEY = "mnemos"


def router_enabled(env: dict[str, str] | None = None) -> bool:
    """The single-connector router runs only when COMPOSE_PROFILES includes `router` (docs/router.md)."""
    env = read_env(ROOT / ".env") if env is None else env
    raw = env.get("COMPOSE_PROFILES") or os.environ.get("COMPOSE_PROFILES") or ""
    return "router" in {x.strip() for x in raw.split(",")}


def targets(contexts=None, env: dict[str, str] | None = None) -> list[tuple[str, str, list[str]]]:
    """(key, public host label, compose services) for every Funnel endpoint: one per context, plus the router."""
    out = [(c, f"hub-{c}", [f"ts-{c}", f"gateway-{c}"]) for c in (CONTEXTS if contexts is None else contexts)]
    if router_enabled(env):
        out.append((ROUTER_KEY, "mnemos", ["ts-mnemos", "hub-router"]))
    return out


def recreate_hub(ctx: str, dry_run: bool, services: list[str] | None = None) -> dict[str, Any]:
    """Recrea solo el sidecar+gateway del contexto. Nunca toca Funnel config ni otros servicios."""
    services = services or [f"ts-{ctx}", f"gateway-{ctx}"]
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


# --- Alertas externas (opcionales) ---------------------------------------------------------------

ALERT_COOLDOWN_S = 30 * 60


def alert_config() -> dict[str, Any]:
    """Canales configurados (entorno > .env). Vacío = alertas apagadas."""
    env = read_env(ROOT / ".env")

    def get(key: str, default: str = "") -> str:
        return (os.environ.get(key) or env.get(key) or default).strip()

    try:
        cooldown = int(get("MNEMOS_ALERT_COOLDOWN_S", str(ALERT_COOLDOWN_S)))
    except ValueError:
        cooldown = ALERT_COOLDOWN_S
    return {
        "webhook_url": get("MNEMOS_ALERT_WEBHOOK_URL"),
        "ntfy_topic": get("MNEMOS_ALERT_NTFY_TOPIC"),
        "ntfy_server": get("MNEMOS_ALERT_NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        "name": get("MNEMOS_ALERT_NAME", "Mnemos"),
        "cooldown_s": max(cooldown, 0),
    }


def alerts_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("webhook_url") or cfg.get("ntfy_topic"))


def alert_text(cfg: dict[str, Any], component: str, down: bool) -> tuple[str, str]:
    """(título, cuerpo) sin datos sensibles: solo nombre de instancia y componente."""
    name = cfg.get("name") or "Mnemos"
    if down:
        return f"{name}: {component} caído", f"{component} no responde; el watchdog lo está atendiendo."
    return f"{name}: {component} recuperado", f"{component} volvió a responder."


def send_alert(cfg: dict[str, Any], title: str, body: str, down: bool) -> dict[str, bool]:
    """Manda por cada canal configurado. Nunca lanza; no loggea URLs ni topics (son secretos)."""
    results: dict[str, bool] = {}
    text = f"{title}\n{body}"
    if cfg.get("webhook_url"):
        try:
            r = httpx.post(cfg["webhook_url"], timeout=10, json={
                "text": text,       # Slack / Mattermost / Google Chat
                "content": text,    # Discord
                "title": title, "message": body, "status": "down" if down else "up",
            })
            results["webhook"] = r.status_code < 300
            if r.status_code >= 300:
                log(f"alerta webhook: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            results["webhook"] = False
            log(f"alerta webhook: {type(exc).__name__}")
    if cfg.get("ntfy_topic"):
        try:
            r = httpx.post(f"{cfg['ntfy_server']}/{cfg['ntfy_topic']}", timeout=10, content=body.encode(),
                           headers={"Title": title, "Priority": "high" if down else "default",
                                    "Tags": "rotating_light" if down else "white_check_mark"})
            results["ntfy"] = r.status_code < 300
            if r.status_code >= 300:
                log(f"alerta ntfy: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            results["ntfy"] = False
            log(f"alerta ntfy: {type(exc).__name__}")
    return results


def process_alerts(state: dict, components: dict[str, bool], cfg: dict[str, Any], now: float,
                   sender=send_alert) -> list[dict[str, Any]]:
    """1 alerta por caída y 1 por recuperación por componente, con anti-flapping.

    state["alerts"][comp] = {"down": bool (hay una alerta de caída enviada sin recuperar), "down_at": epoch}.
    - caído y sin alerta abierta → manda "caído", salvo que la última caída se haya avisado hace < cooldown
      (flapping: tampoco se avisa la recuperación de esa caída silenciada).
    - OK con alerta abierta → manda "recuperado" y la cierra.
    Si ningún canal acepta el envío, no se marca y se reintenta en la próxima corrida.
    """
    if not alerts_enabled(cfg):
        return []
    alerts = state.setdefault("alerts", {})
    sent: list[dict[str, Any]] = []
    for comp, ok in components.items():
        cur = alerts.setdefault(comp, {"down": False})
        if not ok and not cur.get("down"):
            last = cur.get("down_at")
            if isinstance(last, (int, float)) and now - float(last) < cfg.get("cooldown_s", ALERT_COOLDOWN_S):
                continue
            title, body = alert_text(cfg, comp, down=True)
            res = sender(cfg, title, body, True)
            if res and not any(res.values()):
                continue  # ningún canal la recibió: reintenta en la próxima corrida
            cur.update(down=True, down_at=now)
            sent.append({"component": comp, "status": "down", "channels": res})
        elif ok and cur.get("down"):
            title, body = alert_text(cfg, comp, down=False)
            res = sender(cfg, title, body, False)
            if res and not any(res.values()):
                continue
            cur.update(down=False, up_at=now)
            sent.append({"component": comp, "status": "up", "channels": res})
    for item in sent:
        log(f"alerta externa: {item['component']} {item['status']} {item['channels']}")
    return sent


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
    for ctx, label, services in targets():
        name = f"{label}.{tailnet}.ts.net"
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
        log(f"{label}: dns={'OK' if entry['dns_ok'] else 'FAIL'} "
            f"mcp={'OK' if entry['mcp_ok'] else 'FAIL'} addrs={entry['dns_addrs'] or '-'}")

        if not entry["ok"]:
            status["ok"] = False
            reason = "dns" if not entry["dns_ok"] else "mcp"
            resolvers = dns.get("resolvers") or {}
            if not entry["dns_ok"] and resolvers and all("error" in v for v in resolvers.values()):
                # Ningún resolver DoH contestó (Mac sin red, recién despierta): no es el Funnel, no recrear.
                entry["no_network"] = True
                log(f"{label}: sin red (DoH inalcanzable), no recreo")
                status["actions"].append({"ctx": ctx, "action": "skip_no_network", "reason": reason})
            elif in_cooldown(state, ctx, epoch, cooldown_s):
                entry["cooldown"] = True
                log(f"{label}: en cooldown, no recreo (último {entry['last_recreate']})")
                status["actions"].append({"ctx": ctx, "action": "skip_cooldown", "reason": reason})
            else:
                log(f"{label}: recreando {' + '.join(services)} (motivo={reason})")
                rec = recreate_hub(ctx, dry_run=dry_run, **({"services": services} if ctx == ROUTER_KEY else {}))
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
                        log(f"{label}: recheck dns={'OK' if entry['dns_ok'] else 'FAIL'} "
                            f"mcp={'OK' if entry['mcp_ok'] else 'FAIL'}")
                        if not entry["ok"]:
                            status["ok"] = False
                        elif status["ok"] is not False:
                            pass
        # Notificaciones por transición
        was_ok = prev.get(ctx)
        if notify and was_ok is not None:
            if was_ok and not entry["ok"]:
                notify_macos("AI Hub · Funnel caído", f"{label}: DNS/MCP falló; watchdog actuó")
            elif (not was_ok) and entry["ok"]:
                notify_macos("AI Hub · Funnel OK", f"{label}: recuperado")
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

    if notify and not dry_run:
        # Sin red (Mac recién despierta): no es una caída del hub, no alertar.
        components = {f"hub-{c}": bool(h.get("ok")) for c, h in status["hubs"].items() if not h.get("no_network")}
        components["cognee"] = bool(status["cognee"].get("ok"))
        components["dashboard"] = bool(status["dashboard"].get("ok"))
        sent = process_alerts(state, components, alert_config(), time.time())
        if sent:
            status["alerts"] = [{k: a[k] for k in ("component", "status")} for a in sent]

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
        ctx: (state.get("recreates") or {}).get(ctx) for ctx, _, _ in targets()
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
    ap.add_argument("--no-notify", action="store_true", help="sin notificación macOS ni alertas externas")
    ap.add_argument("--test-alert", action="store_true", help="mandar una alerta de prueba y salir")
    ap.add_argument("--cooldown", type=int, default=COOLDOWN_S, help="segundos de cooldown por hub tras recreate")
    ap.add_argument("--wait-after-recreate", type=float, default=25.0,
                    help="segundos a esperar antes del recheck tras recreate")
    args = ap.parse_args()
    if args.test_alert:
        cfg = alert_config()
        if not alerts_enabled(cfg):
            raise SystemExit("alertas apagadas: configurá MNEMOS_ALERT_WEBHOOK_URL o MNEMOS_ALERT_NTFY_TOPIC en .env")
        res = send_alert(cfg, f"{cfg['name']}: alerta de prueba", "Si ves esto, las alertas del watchdog funcionan.", False)
        print(res)
        sys.exit(0 if res and all(res.values()) else 1)
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
