"""Mnemos single-process mode: every context in ONE Python process, no Docker, no Cognee, no Infisical.

    mnemos-local init  [--home ~/Mnemos] [--contexts personal,work] [--owner Name]
    mnemos-local serve [--home ~/Mnemos] [--port 8765]        # http://127.0.0.1:8765/mcp (all spaces, one URL)
    mnemos-local stdio [--home ~/Mnemos]                      # all spaces, for Claude Desktop / Cursor / Codex
    mnemos-local stdio [--home ~/Mnemos] --context personal   # one space only (older configs, .mcpb)
    mnemos-local hygiene [--home ~/Mnemos]                    # cleanup proposals now (serve runs it nightly)
    mnemos-local token [--home ~/Mnemos]                      # print the local bearer token
    mnemos-local set-password [--home ~/Mnemos] < password    # store a login password hash (auth.json, 0600)

Everything lives in one directory (MNEMOS_HOME, default ~/Mnemos):
    contexts.yaml        contexts (same format as config/contexts.yaml)
    secret-policy.yaml   optional (no secret store wired in local mode yet: secret_http_request is off)
    skills/<ctx>/...     skills, plain folders
    data/<ctx>/          ledger, search index, audit log per context
    data/notes/          notes, one SQLite file per dataset (localstore.py)
    token                bearer token for the HTTP listener (0600)

One connector serves every space (local_router.py): tools take an optional `context` and default to the
app's default space (data/apps.json). /<ctx>/mcp still serves one space each, for older configs.
HTTP listens on 127.0.0.1 only and needs `Authorization: Bearer <token>`; Host must be 127.0.0.1/localhost
(DNS-rebinding guard). stdio needs no auth: the client that spawned the process owns it.
Smart parts (import facts, cleanup, semantic search): local_engine picks API (opt-in) / Ollama / the built-in
model (downloaded into <home>/models from the app) / rules. HUB_OLLAMA_URL + HUB_EMBED_MODEL, HUB_ANSWER_MODEL
still point at a specific Ollama.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
import secrets
import sys
from pathlib import Path

DEFAULT_PORT = 8765


def home_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("MNEMOS_HOME") or Path.home() / "Mnemos").expanduser()


def init_home(home: Path, contexts: list[str], owner: str = "me") -> list[str]:
    """Create the home layout (idempotent: never overwrites existing files). Returns what it created."""
    import re
    bad = [c for c in contexts if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", c)]
    if bad or not contexts:
        raise SystemExit(f"invalid context names: {bad or contexts}")
    made = []
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    cf = home / "contexts.yaml"
    if not cf.exists():
        lines = [f"owner: {owner}", "", "contexts:"] + [f"  {c}:\n    description: \"{c}\"" for c in contexts]
        cf.write_text("\n".join(lines) + "\n")
        made.append(str(cf))
    for c in contexts:
        for d in (home / "skills" / c, home / "data" / c):
            if not d.exists():
                d.mkdir(parents=True)
                made.append(str(d))
    (home / "skills" / "shared").mkdir(parents=True, exist_ok=True)
    tok = home / "token"
    if not tok.exists():
        tok.write_text(secrets.token_urlsafe(32))
        os.chmod(tok, 0o600)
        made.append(str(tok))
    return made


def set_password(home: Path, password: str, user: str = "me") -> None:
    """Login password for when the hub is reached from outside this computer (built-in OAuth, Phase 2).
    Only the scrypt hash is stored."""
    import json
    from .local_auth import hash_password
    if len(password) < 8:
        raise SystemExit("password: at least 8 characters")
    f = home / "auth.json"
    f.write_text(json.dumps({"user": user, "password_hash": hash_password(password)}))
    os.chmod(f, 0o600)


def _env(home: Path) -> None:
    if not (home / "contexts.yaml").exists():
        raise SystemExit(f"{home} is not initialised: run `mnemos-local init --home {home}`")
    os.environ["HUB_CONTEXTS_FILE"] = str(home / "contexts.yaml")  # read at import by hub_gateway.contexts


def build_all(home: Path, port: int, only: str | None = None):
    """{context: FastMCP} sharing one note store root; each context gets its own allowed dataset ids."""
    _env(home)
    from .app import build_server
    from .config import Settings
    from .contexts import ALL_DATASET_NAMES, CONTEXTS
    from .instructions import local_instructions
    from . import local_engine
    from .local_engine import EmbedProxy
    from .local_search import LocalIndex
    from .localstore import LocalNoteStore, dataset_id
    from .memory import DatasetMap, MemoryScope
    from .secrets import SecretBroker

    engine = local_engine.get(home)
    dmap = DatasetMap({n: dataset_id(n) for n in ALL_DATASET_NAMES})
    names = {dataset_id(n): n for n in ALL_DATASET_NAMES}
    out = {}
    for name, spec in CONTEXTS.items():
        if only and name != only:
            continue
        data = home / "data" / name
        data.mkdir(parents=True, exist_ok=True)
        st = Settings(context=name, public_url=f"http://127.0.0.1:{port}/{name}", dev_no_auth=True,
                      allowed_logins=frozenset(), github_client_id=None, github_client_secret=None,
                      jwt_signing_key=None, storage_encryption_key=None, data_dir=str(data), cognee_url="",
                      cognee_api_key=None, datasets_file="", skills_root=str(home),  # SkillIndex scans <root>/skills/<owner>/
                      secret_policy_file=str(home / "secret-policy.yaml"), infisical_url=None,
                      infisical_client_id=None, infisical_client_secret=None, infisical_environment="prod",
                      host="127.0.0.1", port=port, auth_provider="local")
        store = LocalNoteStore(home / "data" / "notes", MemoryScope(spec, dmap).allowed_ids(), names)
        index = LocalIndex(str(data / "search.sqlite"), EmbedProxy(engine))  # built-in/Ollama embeddings when present
        from . import local_evidence

        async def evidence(q: str, k: int, _ctx: str = name) -> list:
            return await local_evidence.search(home, _ctx, q, k)
        out[name] = build_server(st, cognee=store, datasets=dmap, broker=SecretBroker({}, None), index=index,
                                 evidence=evidence)
        out[name].mnemos_local = {"store": store, "scope": MemoryScope(spec, dmap), "data": data}  # Graph view reads
        out[name].instructions = local_instructions(spec)  # proactive: this is the user's own memory
    return out


def _descriptions(servers: dict) -> dict[str, str]:
    from . import contexts as _c
    return {n: _c.CONTEXTS[n].description if n in _c.CONTEXTS else n for n in servers}


def build_router(home: Path, servers: dict, via: str = "local", port: int = DEFAULT_PORT):
    """The single connector over `servers` (from build_all). It reloads them when contexts.yaml changes (Spaces
    screen: descriptions, bridges), also inside stdio processes launched by AI apps."""
    from . import contexts as _c
    from .local_activity import Activity
    from .local_router import LocalRouter
    act = Activity(home)
    lr = LocalRouter(home, servers, _descriptions(servers), language=_c.LANGUAGE, via=via, on_event=act.record)

    def reload() -> None:
        _c.reload(home / "contexts.yaml")
        fresh = build_all(home, port)
        servers.update(fresh)
        for gone in [k for k in servers if k not in fresh]:
            servers.pop(gone, None)
        lr.set_descriptions(_descriptions(servers))
    lr.reloader = reload
    return lr.build()


BROWSER_HINT = b"""<!doctype html><meta charset=utf-8><title>Mnemos</title>
<body style="font:16px system-ui;max-width:640px;margin:8vh auto;padding:0 16px">
<h1>This address is for AI apps, not for your browser</h1>
<p>Mnemos is running fine. Addresses ending in <code>/mcp</code> are how Claude, ChatGPT or Cursor talk to your
memory; opening them in a browser shows nothing useful.</p>
<p>To connect an app, use <b>Connect to Claude Desktop</b> in Mnemos Desktop (it fills in the address and your
private token for you), or install the Mnemos extension (.mcpb) in Claude Desktop.</p>
<p><a href="/">Back to the Mnemos status page</a></p></body>"""


class TokenGate:
    """Bearer token + Host check in front of the local HTTP app."""

    def __init__(self, app, token: str, port: int) -> None:
        self.app, self.token = app, token.encode()
        self.hosts = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = dict(scope.get("headers") or [])
            p = scope.get("path") or ""
            ui = p in ("/", "/app") or p.startswith(("/ui/", "/api/"))  # status page + local app: own cookie auth
            if ui and h.get(b"host") in self.hosts:
                await self.app(scope, receive, send)
                return
            auth = h.get(b"authorization", b"")
            ok = auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token)
            if (not ok and h.get(b"host") in self.hosts and scope.get("method") == "GET"
                    and b"text/html" in h.get(b"accept", b"")):  # a person opened an AI address in a browser
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"),
                                        (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'")]})
                await send({"type": "http.response.body", "body": BROWSER_HINT})
                return
            if h.get(b"host") not in self.hosts or not ok:
                code = 403 if h.get(b"host") not in self.hosts else 401
                await send({"type": "http.response.start", "status": code,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def http_app(home: Path, port: int):
    from fastmcp.server.http import create_streamable_http_app
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    servers = build_all(home, port)
    subs = {c: create_streamable_http_app(server=m, streamable_http_path="/mcp") for c, m in servers.items()}
    router = build_router(home, servers, via="http", port=port)
    router_app = create_streamable_http_app(server=router, streamable_http_path="/mcp")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for s in [router_app, *subs.values()]:
                await stack.enter_async_context(s.router.lifespan_context(s))
            task = None
            from . import local_engine as _le
            _le.get(home).autostart()  # 0.4 → 0.5 model upgrade / resume an interrupted download, in the background
            ui.miner.resume_on_start()  # full-history mining continues where it stopped
            if os.environ.get("MNEMOS_HYGIENE", "1") != "0":
                from .local_hygiene import nightly
                task = asyncio.create_task(nightly(home, list(subs), ui.call))
            yield
            if task:
                task.cancel()

    async def home_page(_r):
        from starlette.responses import HTMLResponse
        rows = f"<li><code>http://127.0.0.1:{port}/mcp</code> (spaces: {', '.join(subs)})</li>"
        return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>Mnemos</title>
<body style="font:16px system-ui;max-width:640px;margin:8vh auto;padding:0 16px">
<h1>Mnemos is running</h1><p>Local mode · {len(subs)} spaces. One address for AI apps (every space):</p><ul>{rows}</ul>
<p><a href="/app">Open my memory</a> (notes, graph, skills)</p>
<p style="color:#666">These are addresses for AI apps (Claude, ChatGPT, Cursor), not web pages: opening them here
will not show your notes. Each needs your private local token; use <b>Connect apps</b> in Mnemos Desktop so you never copy addresses or
tokens by hand.</p></body>""",
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    async def health(_r):
        return JSONResponse({"ok": True, "contexts": list(subs), "mode": "local"})

    from .local_ui import UI
    token = os.environ.get("MNEMOS_TOKEN") or (home / "token").read_text().strip()  # Desktop: from the Keychain
    ui = UI(servers, home, token)
    from . import local_hygiene as lh

    async def _pending(_b):
        return lh.pending(home)

    async def _act(b):
        return await lh.act(home, b, ui.call)
    from . import local_spaces as lsp

    async def _spaces(_b):
        return lsp.read(home)

    async def _space_update(b):
        out = lsp.update_space(home, str(b.get("name", "")), b.get("label"), b.get("color"), b.get("description"))
        router.router.reload_now()
        return out

    async def _bridge(b):
        out = lsp.set_bridge(home, str(b.get("from", "")), str(b.get("to", "")), bool(b.get("on")))
        router.router.reload_now()
        return out
    from .local_activity import Activity
    activity = Activity(home)

    async def _apps(_b):
        out = router.router.apps.listing()
        st = activity.stats()
        for a in out["apps"]:
            a["stats"] = st.get(a["key"], {"read": 0, "write": 0, "denied": 0})
        return out

    async def _app_update(b):
        key = str(b.get("key", ""))
        if "default_space" in b:
            router.router.apps.set_default_space(str(b["default_space"]))
            return await _apps(b)
        fields = {k: b[k] for k in ("default", "spaces", "paused", "revoked") if k in b}
        router.router.apps.update(key, **fields)
        return await _apps(b)

    async def _activity(b):
        try:
            limit = int(b.get("limit") or 100)
        except ValueError:
            limit = 100
        return {"events": activity.recent(limit, str(b.get("app") or "") or None)}
    ui.hooks[("GET", "apps")] = _apps
    ui.hooks[("POST", "apps/update")] = _app_update
    ui.hooks[("GET", "activity")] = _activity
    ui.hooks[("GET", "spaces")] = _spaces
    ui.hooks[("POST", "spaces/update")] = _space_update
    ui.hooks[("POST", "spaces/bridge")] = _bridge
    ui.hooks[("GET", "hygiene")] = _pending
    ui.hooks[("POST", "hygiene/act")] = _act
    from . import local_engine
    local_engine.get(home).install(ui)
    from .local_discover import Discover
    from .local_mine import Miner
    disc = Discover(ui)
    disc.install()
    from . import local_evidence

    async def _evidence(src: dict, batch: str) -> None:
        await local_evidence.add_batch(home, src, batch)
    ui.miner = Miner(ui, disc, on_batch=_evidence)
    ui.miner.install()
    app = Starlette(routes=[Route("/", home_page), Route("/health", health), *ui.routes(),
                            Route("/mcp", endpoint=router_app, methods=["GET", "POST", "DELETE"]),
                            *[Mount(f"/{c}", app=s) for c, s in subs.items()]], lifespan=lifespan)
    app.state.ui = ui
    return TokenGate(app, token, port)


def _exit_with_parent() -> None:
    """Desktop sets MNEMOS_PARENT_PID: if the app dies without a clean quit (killed, crashed), stop too instead of
    holding the port and blocking the next start."""
    pid = int(os.environ.get("MNEMOS_PARENT_PID") or 0)
    if pid <= 1:
        return
    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass
            if os.getppid() == 1:
                os._exit(0)
    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mnemos-local", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "serve", "stdio", "token", "set-password", "hygiene"])
    ap.add_argument("--home")
    ap.add_argument("--contexts", default="personal,work")
    ap.add_argument("--owner", default="me")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MNEMOS_LOCAL_PORT") or DEFAULT_PORT))
    ap.add_argument("--context", help="stdio: which context this process serves")
    a = ap.parse_args(argv)
    home = home_dir(a.home)
    if a.cmd == "init":
        made = init_home(home, [c.strip() for c in a.contexts.split(",") if c.strip()], a.owner)
        print(f"Mnemos home: {home}" + ("".join(f"\n  created {m}" for m in made) if made else " (already set up)"))
        return 0
    if a.cmd == "set-password":
        set_password(home, sys.stdin.readline().rstrip("\n"))
        print("password saved")
        return 0
    if a.cmd == "hygiene":  # one cleanup pass now (serve also runs it nightly)
        from .local_hygiene import run
        from .local_ui import UI
        servers = build_all(home, a.port)
        rep = asyncio.run(run(home, list(servers), UI(servers, home, "").call))
        print(f"{len(rep['items'])} cleanup proposals ({rep['mode']}) → {home / 'data' / 'hygiene.json'}")
        return 0
    if a.cmd == "token":
        print(os.environ.get("MNEMOS_TOKEN") or (home / "token").read_text().strip())
        return 0
    if a.cmd == "stdio":
        if not a.context:  # one connector, every space (Connect apps writes this)
            build_router(home, build_all(home, a.port), via="stdio", port=a.port).run(transport="stdio",
                                                                                      show_banner=False)
            return 0
        servers = build_all(home, a.port, only=a.context)
        if a.context not in servers:
            raise SystemExit(f"unknown context {a.context!r}")
        servers[a.context].run(transport="stdio", show_banner=False)
        return 0
    _exit_with_parent()
    import uvicorn
    uvicorn.run(http_app(home, a.port), host="127.0.0.1", port=a.port, log_level="info")
    # Stopped (Quit / SIGTERM): skip C++ static teardown. llama.cpp's Metal backend can GGML_ASSERT there while a
    # built-in model is still resident, which macOS would report as a crash. All data is already committed.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
