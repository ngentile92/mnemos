"""Mnemos single-process mode: every context in ONE Python process, no Docker, no Cognee, no Infisical.

    mnemos-local init  [--home ~/Mnemos] [--contexts personal,work] [--owner Name]
    mnemos-local serve [--home ~/Mnemos] [--port 8765]        # http://127.0.0.1:8765/<ctx>/mcp
    mnemos-local stdio [--home ~/Mnemos] --context personal   # for Claude Desktop (.mcpb) / Cursor stdio
    mnemos-local token [--home ~/Mnemos]                      # print the local bearer token
    mnemos-local set-password [--home ~/Mnemos] < password    # store a login password hash (auth.json, 0600)

Everything lives in one directory (MNEMOS_HOME, default ~/Mnemos):
    contexts.yaml        contexts (same format as config/contexts.yaml)
    secret-policy.yaml   optional (no secret store wired in local mode yet: secret_http_request is off)
    skills/<ctx>/...     skills, plain folders
    data/<ctx>/          ledger, search index, audit log per context
    data/notes/          notes, one SQLite file per dataset (localstore.py)
    token                bearer token for the HTTP listener (0600)

HTTP listens on 127.0.0.1 only and needs `Authorization: Bearer <token>`; Host must be 127.0.0.1/localhost
(DNS-rebinding guard). stdio needs no auth: the client that spawned the process owns it.
Optional: HUB_OLLAMA_URL + HUB_EMBED_MODEL for semantic search, HUB_ANSWER_MODEL for memory_answer.
"""

from __future__ import annotations

import argparse
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
    from .localstore import LocalNoteStore, dataset_id
    from .memory import DatasetMap, MemoryScope
    from .secrets import SecretBroker

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
                      cognee_api_key=None, datasets_file="", skills_root=str(home / "skills"),
                      secret_policy_file=str(home / "secret-policy.yaml"), infisical_url=None,
                      infisical_client_id=None, infisical_client_secret=None, infisical_environment="prod",
                      host="127.0.0.1", port=port, auth_provider="local")
        store = LocalNoteStore(home / "data" / "notes", MemoryScope(spec, dmap).allowed_ids(), names)
        out[name] = build_server(st, cognee=store, datasets=dmap, broker=SecretBroker({}, None))
        out[name].instructions = local_instructions(spec)  # proactive: this is the user's own memory
    return out


class TokenGate:
    """Bearer token + Host check in front of the local HTTP app."""

    def __init__(self, app, token: str, port: int) -> None:
        self.app, self.token = app, token.encode()
        self.hosts = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = dict(scope.get("headers") or [])
            if scope.get("path") == "/" and h.get(b"host") in self.hosts:  # status page: no data, no token
                await self.app(scope, receive, send)
                return
            auth = h.get(b"authorization", b"")
            ok = auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token)
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

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for s in subs.values():
                await stack.enter_async_context(s.router.lifespan_context(s))
            yield

    async def home_page(_r):
        from starlette.responses import HTMLResponse
        rows = "".join(f"<li><code>http://127.0.0.1:{port}/{c}/mcp</code></li>" for c in subs)
        return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>Mnemos</title>
<body style="font:16px system-ui;max-width:640px;margin:8vh auto;padding:0 16px">
<h1>Mnemos is running</h1><p>Local mode · {len(subs)} contexts. Connect an app to one of:</p><ul>{rows}</ul>
<p style="color:#666">Each address needs your local token (Mnemos Desktop fills it in for you).</p></body>""",
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    async def health(_r):
        return JSONResponse({"ok": True, "contexts": list(subs), "mode": "local"})

    app = Starlette(routes=[Route("/", home_page), Route("/health", health), *[Mount(f"/{c}", app=s) for c, s in subs.items()]],
                    lifespan=lifespan)
    return TokenGate(app, (home / "token").read_text().strip(), port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mnemos-local", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "serve", "stdio", "token", "set-password"])
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
    if a.cmd == "token":
        print((home / "token").read_text().strip())
        return 0
    if a.cmd == "stdio":
        if not a.context:
            raise SystemExit("--context is required for stdio")
        servers = build_all(home, a.port, only=a.context)
        if a.context not in servers:
            raise SystemExit(f"unknown context {a.context!r}")
        servers[a.context].run(transport="stdio", show_banner=False)
        return 0
    import uvicorn
    uvicorn.run(http_app(home, a.port), host="127.0.0.1", port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
