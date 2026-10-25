"""Connect an AI from the web app (v0.18): one card per AI, desktop apps first, no trip to the Mnemos window.

What Mnemos learns without asking (only whether apps and config files exist; it never reads an app's chats, caches
or private databases):
- installed:  the app bundle in /Applications (or ~/Applications), or its config folder
- configured: Mnemos is already in that app's MCP config
- seen:       the app really called Mnemos (data/apps.json, written on its first MCP call) — the only "Connected ✓"

Connect writes the same config the Mnemos window used to write (backup first, only the `mnemos` entry):
- Claude Desktop  claude_desktop_config.json  (quit and reopen Claude)
- Claude Code     ~/.claude.json, user scope
- Codex           ~/.codex/config.toml [mcp_servers.mnemos]. The ChatGPT desktop app shares this file, so it gets
                  Mnemos too, but only in its Codex and Work modes (its normal chat only talks to servers online).
- Cursor          its own install link: Cursor asks you to confirm
ChatGPT on the web, Claude on the web and Grok reach only servers on the internet: copy and paste, or Reach from
anywhere (Settings → Advanced).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
import base64
import urllib.parse
from pathlib import Path
from typing import Any

AIS: list[dict[str, Any]] = [
    {"key": "claude-desktop", "name": "Claude Desktop", "kind": "desktop", "app": "Claude"},
    {"key": "chatgpt", "name": "ChatGPT", "kind": "desktop", "app": "ChatGPT", "cloud": "https://chatgpt.com/"},
    {"key": "claude-web", "name": "Claude on the web", "kind": "cloud", "cloud": "https://claude.ai/new"},
    {"key": "cursor", "name": "Cursor", "kind": "desktop", "app": "Cursor"},
    {"key": "claude-code", "name": "Claude Code", "kind": "coding"},
    {"key": "codex", "name": "Codex", "kind": "coding"},
    {"key": "grok", "name": "Grok", "kind": "cloud", "cloud": "https://grok.com/"},
]
KEYS = [a["key"] for a in AIS]


def user_home() -> Path:
    return Path(os.environ.get("MNEMOS_SCAN_HOME") or Path.home())  # same override as the scan and the hooks


def claude_config(h: Path | None = None) -> Path:
    if os.environ.get("MNEMOS_CLAUDE_CONFIG"):
        return Path(os.environ["MNEMOS_CLAUDE_CONFIG"])
    h = h or user_home()
    if sys.platform == "darwin":
        return h / "Library/Application Support/Claude/claude_desktop_config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(h))) / "Claude/claude_desktop_config.json"
    return h / ".config/Claude/claude_desktop_config.json"


def stdio_cmd(home: Path) -> tuple[str, list[str]]:
    """(program, args) an AI app runs to start Mnemos: the same runtime this server runs on."""
    if os.environ.get("MNEMOS_HUB_CMD"):
        parts = shlex.split(os.environ["MNEMOS_HUB_CMD"])
        return parts[0], parts[1:] + ["stdio", "--home", str(home)]
    return sys.executable, ["-I", "-m", "hub_gateway.local", "stdio", "--home", str(home)]


def _app_installed(h: Path, name: str) -> bool:
    return any((d / f"{name}.app").exists() for d in (Path("/Applications"), h / "Applications"))


def _json(p: Path) -> dict | None:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _has_server(p: Path, key: str = "mcpServers") -> bool:
    v = _json(p) or {}
    return isinstance(v.get(key), dict) and "mnemos" in v[key]


def _codex_has(h: Path) -> bool:
    try:
        return "[mcp_servers.mnemos]" in (h / ".codex/config.toml").read_text()
    except OSError:
        return False


def detect(h: Path | None = None) -> dict[str, dict[str, bool]]:
    """{key: {installed, configured}} from file existence and Mnemos' own config entries only."""
    h = h or user_home()
    cc = claude_config(h)
    return {
        "claude-desktop": {"installed": _app_installed(h, "Claude") or cc.parent.exists(), "configured": _has_server(cc)},
        "chatgpt": {"installed": _app_installed(h, "ChatGPT"), "configured": _app_installed(h, "ChatGPT") and _codex_has(h)},
        "claude-web": {"installed": False, "configured": False},
        "cursor": {"installed": _app_installed(h, "Cursor") or (h / ".cursor").exists(), "configured": _has_server(h / ".cursor/mcp.json")},
        "claude-code": {"installed": (h / ".claude.json").exists() or (h / ".claude").is_dir() or bool(shutil.which("claude")),
                        "configured": _has_server(h / ".claude.json")},
        "codex": {"installed": (h / ".codex").is_dir() or bool(shutil.which("codex")), "configured": _codex_has(h)},
        "grok": {"installed": False, "configured": False},
    }


def _backup(p: Path) -> None:
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + f".mnemos-backup-{int(time.time())}"))


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".mnemos-tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def _add_json_server(p: Path, entry: dict, label: str) -> None:
    cfg: dict = {}
    if p.exists() and p.read_text().strip():
        v = _json(p)
        if v is None:
            raise ValueError(f"{label}'s settings file is not valid; Mnemos did not touch it")
        cfg = v
    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{label}'s settings file has an unexpected format; Mnemos did not touch it")
    for k in [k for k, v in servers.items() if k.startswith("mnemos-") and "stdio" in json.dumps(v)]:
        servers.pop(k)  # older one-space entries: the same memory twice otherwise
    servers["mnemos"] = entry
    _backup(p)
    _write_atomic(p, json.dumps(cfg, indent=2) + "\n")


def _toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string is a valid TOML basic string


def _add_codex(h: Path, prog: str, args: list[str]) -> None:
    p = h / ".codex/config.toml"
    old = p.read_text() if p.exists() else ""
    if "[mcp_servers.mnemos]" in old:
        return
    block = f"\n[mcp_servers.mnemos]\ncommand = {_toml_str(prog)}\nargs = [{', '.join(_toml_str(a) for a in args)}]\n"
    _backup(p)
    _write_atomic(p, (old.rstrip("\n") + "\n" + block) if old.strip() else block.lstrip("\n"))


def cursor_link(home: Path) -> str:
    prog, args = stdio_cmd(home)
    cfg = json.dumps({"command": prog, "args": args})
    return "cursor://anysphere.cursor-deeplink/mcp/install?name=mnemos&config=" + urllib.parse.quote(base64.b64encode(cfg.encode()).decode(), safe="")


def connect(key: str, home: Path, h: Path | None = None) -> dict:
    """Put Mnemos in that app's config. Returns {ok, next, open?}: `next` is the one thing to do in the app."""
    h = h or user_home()
    prog, args = stdio_cmd(home)
    if key == "claude-desktop":
        _add_json_server(claude_config(h), {"command": prog, "args": args}, "Claude Desktop")
        return {"ok": True, "next": "Quit Claude Desktop (⌘Q), open it again and send any message."}
    if key == "claude-code":
        _add_json_server(h / ".claude.json", {"type": "stdio", "command": prog, "args": args, "env": {}}, "Claude Code")
        return {"ok": True, "next": "Start a new Claude Code session and send any message."}
    if key in ("codex", "chatgpt"):
        _add_codex(h, prog, args)
        if key == "chatgpt":
            return {"ok": True, "next": "Restart the ChatGPT app, switch to Codex or Work mode and send any message."}
        return {"ok": True, "next": "Start a new Codex session and send any message."}
    if key == "cursor":
        return {"ok": True, "open": cursor_link(home), "next": "Click Install in Cursor, then send any message there."}
    raise ValueError("This AI connects through copy and paste")


def cards(home: Path, seen: dict[str, dict], remote: dict | None = None, h: Path | None = None) -> dict:
    """Every AI with what Mnemos knows about it. `seen` = data/apps.json apps (key -> record)."""
    d = detect(h)
    live = {k for k, r in seen.items() if r.get("last_seen") and not r.get("revoked")}
    out = []
    for a in AIS:
        k = a["key"]
        s = k in live
        if k == "chatgpt":
            s = s or ("codex" in live and d[k]["configured"] and d[k]["installed"])  # its Codex/Work modes call as Codex
        if k == "claude-web":
            s = any(seen[kk].get("via") == "remote" for kk in ("claude-desktop", "claude-web") if kk in live)
        out.append({**a, **d[k], "seen": bool(s)})
    return {"ais": out, "remote": {"enabled": bool((remote or {}).get("enabled")), "url": (remote or {}).get("url")}}


TAG_AI = {"import-claude": ("claude-desktop", "claude-web"), "import-chatgpt": ("chatgpt",)}


def _app_of(source_app: str) -> str:
    """'codex-mcp-client 0.50.0' or 'claude-ai 0.1.0 via mnemos' -> the AI key ('codex', 'claude-desktop')."""
    from .local_apps import app_key
    words = source_app.replace(" via mnemos", "").split()
    while words:
        k = app_key(" ".join(words))[0]
        if k in KEYS:
            return k
        words = words[:-1]
    return ""


def known(items: list[dict]) -> dict[str, int]:
    """How many notes came from each AI: saved by the app itself (source_app) or imported from its files/exports."""
    from .local_discover import KIND_APP
    out = dict.fromkeys(KEYS, 0)
    for it in items:
        hit: set[str] = set()
        app = _app_of(str(it.get("source_app") or ""))
        if app in out:
            hit.add(app)
        for t in it.get("tags") or []:
            hit.update(TAG_AI.get(t, ()))
            if t.startswith("import-") and KIND_APP.get(t[7:]) in out:
                hit.add(KIND_APP[t[7:]])
        if "claude-desktop" in hit:
            hit.add("claude-web")  # same account, same memory
        for k in hit:
            out[k] += 1
    return out
