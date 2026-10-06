#!/usr/bin/env python3
"""Dashboard en vivo del Mnemos — solo lectura, solo 127.0.0.1.

Muestra NOMBRES, ESTADOS y CONTEOS: nunca valores de secretos, tokens ni contenido de la memoria.
Las credenciales que necesita (API keys de Cognee por contexto, identities Viewer de Infisical, restic)
se leen de .env / cognee.env del lado del servidor y jamás se mandan al navegador. Como red de seguridad,
cada respuesta JSON se revisa contra los valores sensibles de .env antes de salir.

Uso:  scripts/dashboard.sh start | stop | status | open     (o: .venv/bin/python3 dashboard/server.py)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
try:
    import httpx
    import yaml
except ModuleNotFoundError:  # python del sistema sin deps → re-ejecutar con el .venv del repo
    if (VENV / "bin" / "python3").exists() and Path(sys.prefix).resolve() != VENV.resolve():
        os.execv(str(VENV / "bin" / "python3"), [str(VENV / "bin" / "python3"), __file__, *sys.argv[1:]])
    raise SystemExit("faltan httpx/pyyaml: creá el venv del repo (.venv) con el gateway instalado")

STATIC = Path(__file__).resolve().parent / "static"
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

PROJECT = "mnemos"
BACKUP_LOG = Path.home() / "Library" / "Logs" / "mnemos-backup.log"
BACKUP_LABEL = f"{LABEL_PREFIX}.backup"
BACKUP_AT = (3, 17)
OFFSITE_LOG = Path.home() / "Library" / "Logs" / "mnemos-offsite.log"
WATCHDOG_STATUS = Path.home() / "Library" / "Logs" / "mnemos-watchdog-status.json"
WATCHDOG_LABEL = f"{LABEL_PREFIX}.watchdog"
STATIC_IMPORT: dict = {}  # opcional: conteos de secretos conocidos por contexto, si Infisical no responde
SENSITIVE_NAME = re.compile(r"(KEY|SECRET|PASSWORD|PASSWD|TOKEN|_PW_|^PW_|_PW$|AUTH|REPOSITORY|DSN)", re.IGNORECASE)
MODEL_VARS = ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL")


# ------------------------------------------------------------------ utilidades
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m or line.lstrip().startswith("#"):
            continue
        v = m.group(2)
        if v[:1] in "\"'" and v.endswith(v[:1]) and len(v) >= 2:
            v = v[1:-1]
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        out[m.group(1)] = v
    return out


def err(e: BaseException) -> str:
    """Error apto para el navegador: solo el tipo y, si es HTTP, el status. Nunca el cuerpo."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, subprocess.TimeoutExpired):
        return "timeout"
    return type(e).__name__


def run(cmd: list[str], timeout: float = 15, env: dict | None = None) -> str:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"exit {r.returncode}")
    return r.stdout


class Cache:
    """Cada colector se refresca con su TTL; si falla se devuelve el error (sin detalles)."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def peek(self, name: str) -> dict | None:
        with self._lock:
            hit = self._d.get(name)
        return hit[1] if hit else None

    def stale(self, name: str, ttl: float) -> bool:
        with self._lock:
            hit = self._d.get(name)
        return hit is None or time.time() - hit[0] >= ttl

    def get(self, name: str, ttl: float, fn) -> dict:
        now = time.time()
        with self._lock:
            hit = self._d.get(name)
        if hit and now - hit[0] < ttl:
            return hit[1]
        try:
            val = {"ok": True, **fn()}
        except Exception as e:  # noqa: BLE001 — un colector roto no tumba el dashboard
            val = {"ok": False, "error": err(e)}
        val["checked_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            self._d[name] = (now, val)
        return val


# ------------------------------------------------------------------ colectores
class Hub:
    def __init__(self) -> None:
        self.env = read_env(ROOT / ".env")
        self.cenv = read_env(ROOT / "cognee.env")
        self.tailnet = self.env.get("TS_TAILNET", "")
        self.cache = Cache()
        self.started = time.time()
        self._sensitive = sorted(
            {v for k, v in {**self.env, **self.cenv}.items() if SENSITIVE_NAME.search(k) and len(v) >= 8},
            key=len, reverse=True)

    # -- contenedores
    def containers(self) -> dict:
        out = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
                   "--format", "{{json .}}"])
        items = []
        for line in out.splitlines():
            c = json.loads(line)
            labels = dict(kv.split("=", 1) for kv in c.get("Labels", "").split(",") if "=" in kv)
            status = c.get("Status", "")
            health = "healthy" if "(healthy)" in status else "unhealthy" if "unhealthy" in status else \
                "starting" if "health: starting" in status else None
            items.append({"service": labels.get("com.docker.compose.service", c.get("Names")),
                          "state": c.get("State"), "health": health,
                          "status": re.sub(r"\s*\((healthy|unhealthy|health: starting)\)", "", status),
                          "image": c.get("Image", "").split("/")[-1]})
        items.sort(key=lambda x: x["service"])
        return {"items": items}

    # -- hubs públicos (Funnel)
    def funnel(self) -> dict:
        hubs = {}
        with httpx.Client(timeout=8, follow_redirects=False) as c:
            for ctx in CONTEXTS:
                url = f"https://hub-{ctx}.{self.tailnet}.ts.net"
                h: dict = {"url": url}
                try:
                    t0 = time.perf_counter()
                    r = c.get(f"{url}/healthz")
                    h["latency_ms"] = round((time.perf_counter() - t0) * 1000)
                    h["health_code"] = r.status_code
                    h["up"] = r.status_code == 200 and r.json().get("context") == ctx
                    m = c.get(f"{url}/mcp")
                    h["oauth_required"] = m.status_code == 401 and "www-authenticate" in m.headers
                except Exception as e:  # noqa: BLE001
                    h.update(up=False, error=err(e))
                hubs[ctx] = h
        return {"hubs": hubs}

    # -- policy de secretos (solo nombres → conteos y dominios)
    def policy(self) -> dict:
        pol = ROOT / "config" / "secret-policy.yaml"
        pol = pol if pol.exists() else ROOT / "config" / "secret-policy.example.yaml"
        data = yaml.safe_load(pol.read_text()) or {}
        out = {}
        for ctx in CONTEXTS:
            sec = data.get(ctx) or {}
            domains: dict[str, int] = {}
            for spec in sec.values():
                for h in (spec or {}).get("allowed_hosts", []):
                    d = ".".join(h.split(".")[-2:])
                    domains[d] = domains.get(d, 0) + 1
            out[ctx] = {"count": len(sec), "domains": dict(sorted(domains.items(), key=lambda kv: -kv[1]))}
        return {"contexts": out}

    # -- Infisical: conteos con las identities Viewer de los gateways (viewSecretValue=false)
    def infisical(self) -> dict:
        base = "http://127.0.0.1:8082"
        res: dict = {"contexts": {}, "shared": None}
        with httpx.Client(base_url=base, timeout=20) as c:
            c.get("/api/status").raise_for_status()
            for ctx in CONTEXTS:
                cid, sec = self.env.get(f"INF_MI_{ctx.upper()}_ID"), self.env.get(f"INF_MI_{ctx.upper()}_SECRET")
                if not cid or not sec:
                    res["contexts"][ctx] = {"ok": False, "error": "sin identity"}
                    continue
                try:
                    r = c.post("/api/v1/auth/universal-auth/login", json={"clientId": cid, "clientSecret": sec})
                    r.raise_for_status()
                    hdr = {"Authorization": f"Bearer {r.json()['accessToken']}"}
                    del r
                    pr = c.get("/api/v1/projects", params={"type": "secret-manager"}, headers=hdr)
                    pr.raise_for_status()
                    ids = {p["slug"]: p["id"] for p in pr.json().get("projects", [])}
                    res["contexts"][ctx] = self._count(c, hdr, ids.get(f"hub-{ctx}"))
                    if res["shared"] is None and "hub-shared" in ids:
                        res["shared"] = self._count(c, hdr, ids["hub-shared"])
                    res["contexts"][ctx]["sees_projects"] = sorted(ids)
                except Exception as e:  # noqa: BLE001
                    res["contexts"][ctx] = {"ok": False, "error": err(e)}
        return res

    @staticmethod
    def _count(c: httpx.Client, hdr: dict, pid: str | None) -> dict:
        if not pid:
            return {"ok": False, "error": "sin acceso al proyecto"}
        q = {"projectId": pid, "environment": "prod", "secretPath": "/", "recursive": "true",
             "viewSecretValue": "false", "includeImports": "false"}
        r = c.get("/api/v4/secrets", params=q, headers=hdr)
        r.raise_for_status()
        secrets = r.json().get("secrets", [])
        folders: dict[str, int] = {}
        for s in secrets:
            p = s.get("secretPath") or "/"
            folders[p] = folders.get(p, 0) + 1
        n = len(secrets)
        del secrets, r  # no retener nada de la respuesta
        return {"ok": True, "count": n, "folders": dict(sorted(folders.items()))}

    # -- Cognee: datasets y conteos (API key de cada contexto; nunca contenido)
    def cognee(self) -> dict:
        base = "http://127.0.0.1:8010"
        hr = httpx.get(f"{base}/health", timeout=8)
        info = hr.json() if hr.status_code == 200 else {}
        if hr.headers.get("server") != "uvicorn" or "version" not in info:
            raise RuntimeError("no es Cognee")  # no mandar keys a otra cosa escuchando en :8010
        state = json.loads((ROOT / "config" / "cognee-datasets.json").read_text())
        ds_ids: dict[str, str] = state.get("datasets", {})
        want = {c: list(spec.own_dataset_names) for c, spec in CONTEXTS.items()}
        want[next(iter(want))].append("shared")  # shared se cuenta una vez, con el primer contexto
        datasets = {}
        with httpx.Client(base_url=base, timeout=20) as c:
            for ctx, names in want.items():
                key = self.env.get(f"COGNEE_KEY_{ctx.upper()}")
                if not key:
                    continue
                hdr = {"X-Api-Key": key}
                for n in names:
                    did = ds_ids.get(n)
                    if not did:
                        continue
                    d = {"context": "shared" if n == "shared" else ctx}
                    try:
                        r = c.get(f"/api/v1/datasets/{did}/data/count", headers=hdr)
                        r.raise_for_status()
                        body = r.json()
                        d["items"] = body.get("count") if isinstance(body, dict) else body
                        # nodos/aristas reales del grafo del dataset (graph-summary queda viejo: se calcula
                        # solo al terminar un pipeline y reportaba 0 en datasets con grafo)
                        g = c.get(f"/api/v1/datasets/{did}/graph", headers=hdr)
                        if g.status_code == 200:
                            gj = g.json()
                            d["nodes"], d["edges"] = len(gj.get("nodes", [])), len(gj.get("edges", []))
                            del gj
                        ps = c.get(f"/api/v1/datasets/{did}/processing-status", headers=hdr)
                        if ps.status_code == 200:
                            d["pending"] = ps.json().get("pending")
                    except Exception as e:  # noqa: BLE001
                        d["error"] = err(e)
                    datasets[n] = d
        return {"version": str(info.get("version", "")), "datasets": datasets}

    # -- LLM
    def llm(self) -> dict:
        cfg = {k: self.cenv.get(k, "") for k in MODEL_VARS}
        out: dict = {"config": cfg}
        try:
            with httpx.Client(base_url="http://127.0.0.1:11434", timeout=4) as c:
                out["ollama_version"] = c.get("/api/version").json().get("version")
                out["installed"] = sorted(m["name"] for m in c.get("/api/tags").json().get("models", []))
                out["loaded"] = sorted(m["name"] for m in c.get("/api/ps").json().get("models", []))
                out["ollama_up"] = True
        except Exception as e:  # noqa: BLE001
            out.update(ollama_up=False, ollama_error=err(e))
        return out

    # -- skills (repo mnemos-skills clonado por skills-sync)
    def skills(self) -> dict:
        out = run(["docker", "exec", f"{PROJECT}-skills-sync-1", "sh", "-c",
                   'cd /skills && git log -1 --format="%h|%cI" && ls -d skills/*/*/SKILL.md 2>/dev/null'])
        lines = out.strip().splitlines()
        head, when = (lines[0].split("|") + [""])[:2] if lines else ("", "")
        by_owner: dict[str, list[str]] = {o: [] for o in ("shared", *CONTEXTS)}
        for p in lines[1:]:
            parts = p.split("/")
            if len(parts) == 4:
                by_owner.setdefault(parts[1], []).append(parts[2])
        return {"head": head, "updated": when, "owners": by_owner, "total": sum(map(len, by_owner.values()))}

    # -- backup (log del LaunchAgent + restic snapshots, cacheado largo)
    def backup(self) -> dict:
        out: dict = {"schedule": f"{BACKUP_AT[0]:02d}:{BACKUP_AT[1]:02d} todos los días"}
        now = dt.datetime.now().astimezone()
        nxt = now.replace(hour=BACKUP_AT[0], minute=BACKUP_AT[1], second=0, microsecond=0)
        if nxt <= now:
            nxt += dt.timedelta(days=1)
        out["next_run"] = nxt.isoformat(timespec="minutes")
        try:
            ll = run(["launchctl", "list"], timeout=5)
            row = next((line.split("\t") for line in ll.splitlines() if line.endswith(BACKUP_LABEL)), None)
            out["agent_loaded"] = row is not None
            out["agent_last_exit"] = row[1] if row else None
        except Exception as e:  # noqa: BLE001
            out["agent_error"] = err(e)
        if BACKUP_LOG.exists():
            text = BACKUP_LOG.read_text(errors="replace").splitlines()
            idx = max((i for i, line in enumerate(text) if line.startswith("== Inicio backup")), default=None)
            if idx is not None:
                chunk = text[idx:]
                stamp = chunk[0].split()[-1]
                out["last_run"] = dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S").astimezone().isoformat(timespec="minutes")
                for line in chunk:
                    if m := re.match(r"snapshot (\w+) saved", line):
                        out["snapshot"] = m.group(1)
                    elif m := re.match(r"Added to the repository: (.+?) \((.+?) stored\)", line):
                        out["added"], out["stored"] = m.group(1), m.group(2)
                    elif m := re.match(r"processed (\d+) files, (.+?) in (\S+)", line):
                        out["files"], out["size"], out["duration"] = int(m.group(1)), m.group(2), m.group(3)
                out["success"] = any(line.startswith("Backup OK") for line in chunk)
        out["restic"] = self.cache.get("restic", 900, self._restic)
        out["offsite"] = self._offsite()
        return out

    @staticmethod
    def _offsite() -> dict:
        # log de scripts/offsite_sync.sh (rclone → Google Drive): última corrida, ok/falló, tamaño remoto
        if not OFFSITE_LOG.exists():
            return {}
        text = OFFSITE_LOG.read_text(errors="replace").splitlines()
        idx = max((i for i, line in enumerate(text) if line.startswith("== Inicio offsite")), default=None)
        if idx is None:
            return {}
        chunk = text[idx:]
        stamp = chunk[0].split()[-1]
        out: dict = {"last_run": dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S").astimezone().isoformat(timespec="minutes")}
        for line in chunk:
            if m := re.match(r"Offsite OK \(\S+\) → (\S+) · (.*)$", line):
                out.update(success=True, remote=m.group(1), size=m.group(2))
            elif m := re.match(r"Offsite FAIL \(\S+\): (.*)$", line):
                out.update(success=False, error=m.group(1))
        return out

    def _restic(self) -> dict:
        repo, pw = self.env.get("RESTIC_REPOSITORY"), self.env.get("RESTIC_PASSWORD")
        if not repo or not pw:
            raise RuntimeError("sin restic en .env")
        env = {**os.environ, "RESTIC_REPOSITORY": repo, "RESTIC_PASSWORD": pw}
        restic = next((p for p in ("/opt/homebrew/bin/restic", "/usr/local/bin/restic") if Path(p).exists()), "restic")
        snaps = json.loads(run([restic, "snapshots", "--json", "--tag", "mnemos"], timeout=90, env=env) or "[]")
        snaps.sort(key=lambda s: s.get("time", ""))
        last = snaps[-1] if snaps else {}
        summ = last.get("summary") or {}
        return {"snapshots": len(snaps), "latest": last.get("short_id"), "latest_time": (last.get("time") or "")[:19],
                "latest_total_bytes": summ.get("total_bytes_processed")}


    # -- watchdog Funnel/DNS (status JSON de scripts/hub_watchdog.py)
    def watchdog(self) -> dict:
        out: dict = {"schedule": "cada 15 min", "status_path": str(WATCHDOG_STATUS)}
        try:
            ll = run(["launchctl", "list"], timeout=5)
            row = next((line.split("\t") for line in ll.splitlines() if line.endswith(WATCHDOG_LABEL)), None)
            out["agent_loaded"] = row is not None
            out["agent_last_exit"] = row[1] if row else None
        except Exception as e:  # noqa: BLE001
            out["agent_error"] = err(e)
        if not WATCHDOG_STATUS.exists():
            out["has_status"] = False
            return out
        try:
            data = json.loads(WATCHDOG_STATUS.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {**out, "has_status": False, "error": err(e)}
        out["has_status"] = True
        out["last_run"] = data.get("finished_at") or data.get("started_at")
        out["run_ok"] = bool(data.get("ok"))
        hubs = {}
        for ctx in CONTEXTS:
            h = (data.get("hubs") or {}).get(ctx) or {}
            lr = (data.get("last_recreates") or {}).get(ctx) or {}
            hubs[ctx] = {
                "ok": h.get("ok"), "dns_ok": h.get("dns_ok"), "mcp_ok": h.get("mcp_ok"),
                "recreated": h.get("recreated"), "cooldown": h.get("cooldown"),
                "last_recreate": h.get("last_recreate") or lr.get("at"),
                "last_recreate_reason": lr.get("reason"),
            }
        out["hubs"] = hubs
        out["cognee_ok"] = (data.get("cognee") or {}).get("ok")
        out["dashboard_ok"] = (data.get("dashboard") or {}).get("ok")
        out["actions"] = data.get("actions") or []
        return out

    # -- repo
    def git(self) -> dict:
        log = run(["git", "log", "-8", "--format=%h%x1f%cI%x1f%s"])
        commits = [dict(zip(("hash", "date", "subject"), line.split("\x1f"))) for line in log.splitlines()]
        branch = run(["git", "status", "-sb"]).splitlines()[0].removeprefix("## ")
        return {"branch": branch, "commits": commits}

    # ------------------------------------------------------------------
    JOBS: ClassVar[dict[str, int]] = {"containers": 10, "funnel": 20, "policy": 30, "infisical": 120, "cognee": 60, "llm": 30,
            "skills": 60, "backup": 60, "watchdog": 30, "git": 30}

    def refresher(self) -> None:
        """Refresca cada colector en segundo plano según su TTL: /api/status responde al instante."""
        busy: set[str] = set()
        with cf.ThreadPoolExecutor(max_workers=len(self.JOBS)) as ex:
            while True:
                for k, ttl in self.JOBS.items():
                    if k not in busy and self.cache.stale(k, ttl):
                        busy.add(k)
                        fut = ex.submit(self.cache.get, k, ttl, getattr(self, k))
                        fut.add_done_callback(lambda _f, k=k: busy.discard(k))
                time.sleep(2)

    def status(self) -> dict:
        data = {k: self.cache.peek(k) or {"ok": None, "pending": True} for k in self.JOBS}
        data["static_import"] = STATIC_IMPORT
        data["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        data["dashboard_uptime_s"] = round(time.time() - self.started)
        return data

    def redact(self, text: str) -> str:
        for v in self._sensitive:
            if v in text:
                text = text.replace(v, "[redactado]")
        return text


# ------------------------------------------------------------------ HTTP
TS_CLI = ("tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
          "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
_TS_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.ts\.net$")


def tailnet_dns_name() -> str | None:
    """MagicDNS de esta Mac (p. ej. my-laptop.tailXXXX.ts.net) según `tailscale status --json`."""
    for exe in TS_CLI:
        try:
            r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5)
            name = str((json.loads(r.stdout).get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if name:
            return name
    return None


def build_allowed_hosts(host: str, port: int, tailnet_port: int = 0, dns_name: str | None = None) -> set[str]:
    """Hosts aceptados en el header Host (anti DNS-rebinding): loopback y, si hay `tailscale serve`
    (solo tailnet, nunca Funnel) en `tailnet_port`, `<nombre-magicdns>:<tailnet_port>`."""
    allowed = {f"{host}:{port}", f"localhost:{port}", f"127.0.0.1:{port}"}
    name = (dns_name or "").rstrip(".").lower()
    if tailnet_port and name and _TS_NAME_RE.match(name):
        allowed.add(f"{name}:{tailnet_port}")
    return allowed


def make_handler(hub: Hub | None, allowed_hosts: set[str], demo: dict | None):
    class H(BaseHTTPRequestHandler):
        server_version = "mnemos-dashboard"
        sys_version = ""

        def log_message(self, fmt, *args):  # sin query strings ni headers en el log
            sys.stderr.write(f"{self.log_date_time_string()} {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; style-src-attr 'unsafe-inline'; "
                             "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # el navegador canceló el fetch (p. ej. cambio de vista en Explorar)

        def _json(self, code: int, data: dict) -> None:
            text = json.dumps(data, ensure_ascii=False, default=str)
            if hub is not None:
                text = hub.redact(text)
            self._send(code, text.encode(), "application/json; charset=utf-8")

        def _explore(self, path: str, q: dict) -> None:
            view = (q.get("view") or [""])[0]
            if demo is not None:
                ex = (demo.get("explore") or {}).get(view)
                if path == "/api/skill":
                    name = (q.get("name") or [""])[0]
                    sk = (demo.get("skills_content") or {}).get(name)
                    return self._json(200, sk) if sk else self._json(404, {"error": "no encontrada"})
                return self._json(200, ex) if ex else self._json(404, {"error": "vista sin datos demo"})
            try:
                if path == "/api/skill":
                    return self._json(200, hub.explorer.skill(view, (q.get("name") or [""])[0][:64]))
                return self._json(200, hub.explorer.explore(view, fresh=bool(q.get("fresh"))))
            except (ValueError, LookupError) as e:
                return self._json(404 if isinstance(e, LookupError) else 400, {"error": str(e)[:120]})
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": err(e)})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if self.headers.get("Host", "").lower() not in allowed_hosts:  # anti DNS-rebinding
                return self._send(421, b"host no permitido", "text/plain; charset=utf-8")
            path, _, query = self.path.partition("?")
            if path in ("/api/explore", "/api/skill"):
                return self._explore(path, parse_qs(query))
            if path == "/api/status":
                data = demo if demo is not None else hub.status()
                text = json.dumps(data, ensure_ascii=False)
                if hub is not None:
                    text = hub.redact(text)
                return self._send(200, text.encode(), "application/json; charset=utf-8")
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"no encontrado", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="solo loopback (o una IP del tailnet, nunca Funnel)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AIHUB_DASHBOARD_PORT", "8787")))
    ap.add_argument("--demo", metavar="JSON", help="servir datos de ejemplo (para capturas sin datos reales)")
    ap.add_argument("--tailnet-port", type=int, default=int(os.environ.get("AIHUB_DASHBOARD_TS_PORT", "8444")),
                    help="puerto HTTPS de `tailscale serve` (solo tailnet) que se acepta en el Host; 0 = solo loopback")
    args = ap.parse_args()
    if args.host in ("0.0.0.0", "::", ""):
        raise SystemExit("no escucho en todas las interfaces: usá 127.0.0.1 (o una IP 100.x del tailnet)")
    demo = json.loads(Path(args.demo).read_text()) if args.demo else None
    hub = None if demo is not None else Hub()
    if hub is not None:
        # import tardío: solo con datos reales (usa credenciales de .env)
        from explore import Explorer

        hub.explorer = Explorer(hub.env, err)
        threading.Thread(target=hub.refresher, name="refresher", daemon=True).start()
    allowed = build_allowed_hosts(args.host, args.port, args.tailnet_port,
                                  tailnet_dns_name() if args.tailnet_port else None)
    extra = sorted(allowed - {f"{args.host}:{args.port}", f"localhost:{args.port}", f"127.0.0.1:{args.port}"})
    if extra:
        print(f"también acepto Host {', '.join(extra)} (tailscale serve, solo tailnet)", flush=True)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(hub, allowed, demo))
    print(f"dashboard en http://{args.host}:{args.port}  (Ctrl+C para cortar)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
