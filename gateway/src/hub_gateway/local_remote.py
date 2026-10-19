"""Reach from anywhere (local mode, opt-in): cloud AI apps (Claude on the web and phone, ChatGPT, Grok) reach
this computer's Mnemos through Tailscale Funnel, signing in with OAuth like they do with a hosted hub.

Off by default. Turning it on:
  1. needs Tailscale installed and signed in on this computer (Funnel gives it a public https://<name>.ts.net);
  2. needs a Mnemos password (auth.json), which is what you type when an app connects;
  3. runs `tailscale funnel --bg --https=<443|8443|10000> http://127.0.0.1:<port>` on a Funnel port nobody else
     uses on this machine (other serve/funnel settings are left alone), and remembers it in data/remote.json.
Requests whose Host is that public name go to a separate app that only has the OAuth endpoints (reused from the
hub router: discovery, dynamic registration, PKCE, refresh rotation, lockout, per-app consent and grants) and
/mcp. Nothing else of the local listener (status page, /app, /api, the per-space addresses, the local token) is
reachable from outside. Turning it off removes only our Funnel entry and refuses that Host again.

Remote apps show up in Mnemos → Apps marked "from anywhere"; "Remove access" also deletes their OAuth grant, so
their tokens stop working at once. Apps only work while this computer is awake and Mnemos is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("mnemos.remote")

FUNNEL_PORTS = (443, 8443, 10000)
MIN_PASSWORD = 10
CANDIDATES = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale",
              "/usr/local/bin/tailscale", "/usr/bin/tailscale")
_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")
Runner = Callable[[list[str], float], tuple[int | None, str]]


def _run(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """(exit code or None on timeout, combined output)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout or b""
        err = e.stderr or b""
        txt = (out.decode() if isinstance(out, bytes) else out) + (err.decode() if isinstance(err, bytes) else err)
        return None, txt
    except OSError as e:
        return 127, str(e)


class Tailscale:
    """The few `tailscale` CLI calls we need. `runner` is replaceable (tests never touch a real Tailscale)."""

    def __init__(self, runner: Runner | None = None, binary: str | None = None) -> None:
        self.runner = runner or _run
        self._binary = binary

    def binary(self) -> str | None:
        if self._binary:
            return self._binary
        found = shutil.which("tailscale")
        if found:
            return found
        return next((c for c in CANDIDATES if os.path.exists(c)), None)

    def run(self, *args: str, timeout: float = 15) -> tuple[int | None, str]:
        b = self.binary()
        if not b:
            return 127, "tailscale is not installed"
        return self.runner([b, *args], timeout)

    def status(self) -> dict[str, Any]:
        if not self.binary():
            return {"installed": False, "running": False}
        code, out = self.run("status", "--json")
        try:
            st = json.loads(out) if code == 0 or out.lstrip().startswith("{") else {}
        except ValueError:
            st = {}
        me = st.get("Self") or {}
        dns = str(me.get("DNSName") or "").rstrip(".")
        caps = set(me.get("Capabilities") or []) | set((me.get("CapMap") or {}).keys())
        return {"installed": True, "running": st.get("BackendState") == "Running", "dns_name": dns,
                "https": bool(st.get("CertDomains")),
                "funnel_allowed": any("funnel" in str(c) for c in caps),
                "login_url": st.get("AuthURL") or ""}

    def serve_config(self) -> dict[str, Any]:
        code, out = self.run("funnel", "status", "--json")
        try:
            return json.loads(out) if code == 0 and out.strip() else {}
        except ValueError:
            return {}

    @staticmethod
    def ours(cfg: dict, host: str, local_port: int) -> int | None:
        """The Funnel port already proxying to us, if any."""
        for hp, web in (cfg.get("Web") or {}).items():
            name, _, p = hp.rpartition(":")
            if name != host or not p.isdigit():
                continue
            for h in (web.get("Handlers") or {}).values():
                proxy = str((h or {}).get("Proxy") or "")
                if proxy.rstrip("/").endswith(f":{local_port}") and ("127.0.0.1" in proxy or "localhost" in proxy):
                    return int(p)
        return None

    @classmethod
    def pick_port(cls, cfg: dict, host: str, local_port: int) -> int | None:
        mine = cls.ours(cfg, host, local_port)
        if mine:
            return mine
        used = {int(p) for p in (cfg.get("TCP") or {}) if str(p).isdigit()}
        return next((p for p in FUNNEL_PORTS if p not in used), None)

    def enable(self, https_port: int, local_port: int) -> dict[str, Any]:
        code, out = self.run("funnel", "--bg", "--yes", f"--https={https_port}", f"http://127.0.0.1:{local_port}",
                             timeout=25)
        if code == 0:
            return {"ok": True}
        m = _URL_RE.search(out or "")
        if m:  # Funnel (or HTTPS certificates) not enabled for this tailnet yet: the admin has to allow it once
            return {"ok": False, "needs": "tailnet-admin", "url": m.group(0).rstrip(".,)"), "output": out[-600:]}
        if "yes" in (out or "") and ("flag provided but not defined" in out or "unknown flag" in out):
            code, out = self.run("funnel", "--bg", f"--https={https_port}", f"http://127.0.0.1:{local_port}",
                                 timeout=25)
            if code == 0:
                return {"ok": True}
            m = _URL_RE.search(out or "")
            if m:
                return {"ok": False, "needs": "tailnet-admin", "url": m.group(0).rstrip(".,)"), "output": out[-600:]}
        return {"ok": False, "error": (out or "tailscale funnel failed").strip()[-600:]}

    def disable(self, https_port: int) -> dict[str, Any]:
        code, out = self.run("funnel", f"--https={https_port}", "off", timeout=15)
        return {"ok": code == 0, "output": (out or "").strip()[-300:]}


def client_key(client_id: str) -> str:
    return "remote-" + hashlib.sha256(client_id.encode()).hexdigest()[:10]


def build_store(home: Path):
    """Encrypted OAuth store (clients, codes, tokens, grants) in <home>/data/oauth-remote. The key is created on
    first use next to it (0600); it never leaves this computer."""
    from cryptography.fernet import Fernet
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    d = home / "data" / "oauth-remote"
    d.mkdir(parents=True, exist_ok=True)
    kf = home / "data" / "remote.key"
    if not kf.exists():
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(Fernet.generate_key().decode())
    return FernetEncryptionWrapper(key_value=FileTreeStore(data_directory=d), fernet=Fernet(kf.read_text().strip().encode()))


def _provider_class():
    import html

    from .router.auth import RouterOAuthProvider

    class RemoteOAuthProvider(RouterOAuthProvider):
        """Router OAuth with local-mode wording: one password (no user name to remember) and spaces by label."""

        spaces_fn: Callable[[], list[tuple[str, str]]] | None = None

        def _refresh(self) -> None:
            if self.spaces_fn:
                self.contexts = [n for n, _ in self.spaces_fn()]

        async def _login_form(self, txn: str, pending: dict, error: str = ""):
            client = await self.get_client(pending["client_id"])
            name = html.escape((client.client_name if client else None) or pending["client_id"])
            dest = html.escape(pending["redirect_uri"].split("/")[2] if "://" in pending["redirect_uri"] else "")
            err = f'<p class="e">{html.escape(error)}</p>' if error else ""
            return self._page(f"""<h2>Mnemos</h2><p><b>{name}</b> wants to connect to your memory on your computer.
Type your Mnemos password, then choose which spaces it may use. You will return to <code>{dest}</code>.</p>{err}
<form method="post" action="/local-login"><input type="hidden" name="txn" value="{html.escape(txn)}">
<input type="hidden" name="username" value="{html.escape(self.user)}">
<input name="password" type="password" autocomplete="current-password" placeholder="Mnemos password" required autofocus>
<button type="submit">Continue</button></form>
<p class="m">Not you, or you did not start this? Just close this tab.</p>""")

        async def _consent_form(self, consent: str, pending: dict, error: str = ""):
            self._refresh()
            labels = dict(self.spaces_fn()) if self.spaces_fn else {}
            client = await self.get_client(pending["client_id"])
            name = html.escape((client.client_name if client else None) or pending["client_id"])
            err = f'<p class="e">{html.escape(error)}</p>' if error else ""
            boxes = "".join(f'<label><input type="checkbox" name="ctx" value="{html.escape(c)}"> '
                            f'{html.escape(labels.get(c) or c)}</label><br>' for c in self.contexts)
            return self._page(scripts=True, body=f"""<h2>Mnemos</h2><p>Which spaces may <b>{name}</b> use? It will not
see the others. Shared stays readable from the spaces you tick.</p>{err}
<form method="post" action="/consent"><input type="hidden" name="consent" value="{html.escape(consent)}">
<input type="hidden" name="switch" value="1">
<fieldset><legend>Spaces</legend>{boxes}</fieldset>
<button type="submit" id="allow">Allow</button></form>
<p class="m">You can pause it or remove its access later in Mnemos → Apps.</p><script src="/consent.js"></script>""")

        async def consent_post(self, request):
            self._refresh()
            return await super().consent_post(request)

    return RemoteOAuthProvider


class Remote:
    """State + the public app. One per `serve` process."""

    def __init__(self, home: Path, port: int, make_router: Callable[..., Any], spaces_fn: Callable[[], list],
                 tailscale: Tailscale | None = None, keep_awake_cmd: list[str] | None = None) -> None:
        self.home, self.port = Path(home), port
        self.file = self.home / "data" / "remote.json"
        self.make_router = make_router  # (via, auth, identify) -> FastMCP (local.build_router)
        self.spaces_fn = spaces_fn
        self.ts = tailscale or Tailscale()
        self.app = None
        self.provider = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._awake: subprocess.Popen | None = None
        self.keep_awake_cmd = keep_awake_cmd if keep_awake_cmd is not None else (
            ["caffeinate", "-is", "-w", str(os.getpid())] if sys.platform == "darwin" else [])
        self._hosts: set[bytes] = set()
        self._sync_hosts()

    # -- state
    def state(self) -> dict[str, Any]:
        try:
            d = json.loads(self.file.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, **kw: Any) -> dict[str, Any]:
        d = {**self.state(), **kw}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.file)
        self._sync_hosts()
        return d

    def enabled(self) -> bool:
        st = self.state()
        return bool(st.get("enabled") and st.get("host") and st.get("https_port"))

    def host_header(self, st: dict | None = None) -> str:
        st = st or self.state()
        host, p = str(st.get("host") or ""), int(st.get("https_port") or 443)
        return host if p == 443 else f"{host}:{p}"

    def public_url(self, st: dict | None = None) -> str:
        return f"https://{self.host_header(st)}"

    def _sync_hosts(self) -> None:
        st = self.state()
        self._hosts = {self.host_header(st).lower().encode()} if st.get("enabled") and st.get("host") else set()

    def matches(self, host: bytes | None) -> bool:
        """Is this request for the public name (only while Reach from anywhere is on)?"""
        return bool(host) and host.lower() in self._hosts

    # -- auth.json (the same password the local app uses when it asks for one)
    def auth(self) -> dict[str, str] | None:
        try:
            d = json.loads((self.home / "auth.json").read_text())
            return d if str(d.get("password_hash", "")).startswith("scrypt:") else None
        except (OSError, ValueError, AttributeError):
            return None

    def set_password(self, password: str) -> None:
        from .local_auth import hash_password
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"choose a password of at least {MIN_PASSWORD} characters")
        f = self.home / "auth.json"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"user": "me", "password_hash": hash_password(password)}))

    # -- the public app
    def get_provider(self):
        if self.provider is None:
            a = self.auth()
            if not a:
                raise ValueError("set a Mnemos password first")
            cls = _provider_class()
            base = self.public_url() if self.state().get("host") else "https://localhost"
            self.provider = cls(base_url=base, contexts=[n for n, _ in self.spaces_fn()],
                                store=build_store(self.home), user=a.get("user") or "me",
                                password_hash=a["password_hash"], passkeys=False)
            self.provider.spaces_fn = self.spaces_fn
        return self.provider

    async def identify(self, client_name: str | None):
        """Who is calling over the public address: the OAuth client, not the name it declares."""
        from fastmcp.server.dependencies import get_access_token

        from .local_apps import app_key
        from .router.auth import granted_contexts
        tok = get_access_token()
        cid = str(getattr(tok, "client_id", "") or "")
        client = await self.get_provider().get_client(cid) if cid else None
        _, label = app_key((client.client_name if client else None) or client_name)
        return client_key(cid), label, granted_contexts(list(getattr(tok, "scopes", None) or [])), {"oauth_client": cid}

    async def start(self) -> None:
        if self.app is not None:
            return
        prov = self.get_provider()
        mcp = self.make_router(via="remote", auth=prov, identify=self.identify)
        app = mcp.http_app(path="/mcp")
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(app.router.lifespan_context(app))
        self._stack, self.app = stack, app
        self._keep_awake(bool(self.state().get("keep_awake", True)))

    async def stop(self) -> None:
        self.app = None
        if self._stack:
            stack, self._stack = self._stack, None
            with contextlib.suppress(Exception):
                await stack.aclose()
        self._keep_awake(False)

    def _keep_awake(self, on: bool) -> None:
        if self._awake and (not on or self._awake.poll() is not None):
            with contextlib.suppress(Exception):
                self._awake.terminate()
            self._awake = None
        if on and not self._awake and self.keep_awake_cmd:
            with contextlib.suppress(OSError):
                self._awake = subprocess.Popen(self.keep_awake_cmd, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- UI actions
    async def status(self, check: bool = False) -> dict[str, Any]:
        st = self.state()
        out: dict[str, Any] = {"enabled": self.enabled(), "has_password": bool(self.auth()),
                               "keep_awake": bool(st.get("keep_awake", True)), "min_password": MIN_PASSWORD,
                               "since": st.get("since")}
        if self.enabled():
            out["url"] = self.public_url(st) + "/mcp"
        if check or not self.enabled():
            ts = await asyncio.to_thread(self.ts.status)
            out["tailscale"] = ts
            if self.enabled() and ts.get("running"):
                cfg = await asyncio.to_thread(self.ts.serve_config)
                out["funnel_on"] = self.ts.ours(cfg, str(st.get("host")), self.port) == int(st.get("https_port") or 0)
        return out

    async def enable(self, password: str = "", keep_awake: bool = True) -> dict[str, Any]:
        if not self.auth():
            if not password:
                return {"ok": False, "needs": "password",
                        "error": f"Choose a Mnemos password (at least {MIN_PASSWORD} characters)."}
            try:
                self.set_password(password)
            except ValueError as e:
                return {"ok": False, "needs": "password", "error": str(e)}
            self.provider = None
        ts = await asyncio.to_thread(self.ts.status)
        if not ts.get("installed"):
            return {"ok": False, "needs": "tailscale", "error": "Install Tailscale first (tailscale.com/download)."}
        if not ts.get("running") or not ts.get("dns_name"):
            return {"ok": False, "needs": "tailscale-login", "url": ts.get("login_url") or "",
                    "error": "Open Tailscale and sign in, then try again."}
        host = ts["dns_name"]
        cfg = await asyncio.to_thread(self.ts.serve_config)
        port = self.ts.pick_port(cfg, host, self.port)
        if not port:
            return {"ok": False, "error": "All Tailscale Funnel ports (443, 8443, 10000) are used by something else "
                                          "on this computer."}
        if self.ts.ours(cfg, host, self.port) != port:
            r = await asyncio.to_thread(self.ts.enable, port, self.port)
            if not r.get("ok"):
                return r
        await self.stop()
        self.provider = None  # base URL may have changed
        self._save(enabled=True, host=host, https_port=port, keep_awake=bool(keep_awake),
                   since=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        await self.start()
        return {"ok": True, **(await self.status())}

    async def disable(self) -> dict[str, Any]:
        st = self.state()
        res: dict[str, Any] = {"ok": True}
        if st.get("https_port"):
            off = await asyncio.to_thread(self.ts.disable, int(st["https_port"]))
            if not off.get("ok"):
                res["warning"] = ("Mnemos no longer answers on the public address, but Tailscale did not confirm "
                                  "turning Funnel off: " + off.get("output", ""))
        self._save(enabled=False)
        await self.stop()
        return {**res, **(await self.status())}

    async def set_keep_awake(self, on: bool) -> dict[str, Any]:
        self._save(keep_awake=bool(on))
        if self.app is not None:
            self._keep_awake(bool(on))
        return await self.status()

    async def revoke(self, client_id: str) -> bool:
        """Remove access: drop the OAuth grant, so every token of that app stops working."""
        if not client_id or not self.auth():
            return False
        return await self.get_provider().revoke_grant(client_id)


