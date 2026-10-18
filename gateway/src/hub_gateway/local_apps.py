"""Which AI apps use this local Mnemos, and what each one may do (local mode).

One file, <home>/data/apps.json, shared by the HTTP server and every stdio process an AI app launches:

    {"default_space": "personal",
     "apps": {"claude-desktop": {"label": "Claude Desktop", "default": "work", "spaces": null,
                                 "first_seen": "...", "last_seen": "...", "via": "stdio"}}}

- An app is identified by the name it declares in MCP `initialize` (clientInfo), normalised (see `app_key`).
  For local apps this is informative, not a secret: anything that can launch Mnemos on this computer already
  owns the data. Remote apps (Reach from anywhere) are identified by their OAuth client instead.
- `default`: the space this app reads and saves to when a call does not pass `context`. None = `default_space`.
- `spaces`: the spaces it may use (None = all). Shared is always readable.
- `paused`: every call is refused until resumed. `revoked`: access removed (refused until "Allow again"; remote
  apps also lose their sign-in).
Writes are atomic (tmp + rename) under a lock file, so two processes never corrupt it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

LABELS = {  # clientInfo.name (lowercase) -> (key, label)
    "claude-ai": ("claude-desktop", "Claude Desktop"),
    "claude-desktop": ("claude-desktop", "Claude Desktop"),
    "claude-code": ("claude-code", "Claude Code"),
    "cursor-vscode": ("cursor", "Cursor"),
    "cursor": ("cursor", "Cursor"),
    "codex-mcp-client": ("codex", "Codex"),
    "codex": ("codex", "Codex"),
    "visual studio code": ("vscode", "VS Code"),
    "visual studio code - insiders": ("vscode", "VS Code"),
    "vscode": ("vscode", "VS Code"),
    "windsurf-client": ("windsurf", "Windsurf"),
    "gemini-cli-mcp-client": ("gemini-cli", "Gemini CLI"),
    "openai-mcp": ("chatgpt", "ChatGPT"),
    "chatgpt": ("chatgpt", "ChatGPT"),
    "grok": ("grok", "Grok"),
}
_KEY_RE = re.compile(r"[^a-z0-9]+")


def app_key(client_name: str | None) -> tuple[str, str]:
    """(stable key, friendly label) for the name an MCP client declares."""
    raw = " ".join(str(client_name or "").split())[:60]
    if not raw:
        return "unknown", "Unknown app"
    hit = LABELS.get(raw.lower())
    if hit:
        return hit
    key = _KEY_RE.sub("-", raw.lower()).strip("-")[:40] or "unknown"
    return key, raw


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Apps:
    def __init__(self, home: Path, contexts: list[str]) -> None:
        self.file = Path(home) / "data" / "apps.json"
        self.contexts = list(contexts)

    # -- storage
    def load(self) -> dict[str, Any]:
        try:
            d = json.loads(self.file.read_text())
            if isinstance(d, dict):
                d.setdefault("apps", {})
                return d
        except (OSError, ValueError):
            pass
        return {"apps": {}}

    @contextlib.contextmanager
    def _locked(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        lock = open(self.file.with_suffix(".lock"), "a+")
        try:
            try:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            except ImportError:  # Windows: best effort
                pass
            d = self.load()
            yield d
            tmp = self.file.with_suffix(f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False))
            os.replace(tmp, self.file)
        finally:
            lock.close()

    # -- reads
    def default_space(self, d: dict | None = None) -> str:
        d = d or self.load()
        ds = d.get("default_space")
        return ds if ds in self.contexts else self.contexts[0]

    def get(self, key: str) -> dict[str, Any] | None:
        return self.load()["apps"].get(key)

    def allowed(self, rec: dict | None) -> list[str]:
        sp = (rec or {}).get("spaces")
        if sp is None:
            return list(self.contexts)
        return [c for c in self.contexts if c in sp]

    def default_for(self, rec: dict | None) -> str | None:
        allowed = self.allowed(rec)
        want = (rec or {}).get("default") or self.default_space()
        if want in allowed:
            return want
        return allowed[0] if allowed else None

    def listing(self) -> dict[str, Any]:
        d = self.load()
        apps = [{"key": k, **v, "allowed": self.allowed(v), "effective_default": self.default_for(v)}
                for k, v in d["apps"].items()]
        apps.sort(key=lambda a: str(a.get("last_seen") or ""), reverse=True)
        return {"default_space": self.default_space(d), "spaces": self.contexts, "apps": apps}

    # -- writes
    def touch(self, key: str, label: str, via: str = "local") -> dict[str, Any]:
        """Register an app the first time it calls, refresh last_seen (at most once a minute)."""
        cur = self.get(key)
        now = _now()
        if cur and str(cur.get("last_seen", ""))[:16] == now[:16]:
            return cur
        with self._locked() as d:
            rec = d["apps"].setdefault(key, {"label": label, "default": None, "spaces": None,
                                             "first_seen": now, "via": via})
            rec["last_seen"] = now
            return dict(rec)

    def update(self, key: str, **fields: Any) -> dict[str, Any]:
        bad = set(fields) - {"default", "spaces", "label", "paused", "revoked"}
        if bad:
            raise ValueError(f"unknown app settings: {sorted(bad)}")
        if "default" in fields and fields["default"] not in (None, *self.contexts):
            raise ValueError(f"unknown space {fields['default']!r}")
        if "spaces" in fields and fields["spaces"] is not None:
            sp = [s for s in fields["spaces"] if s in self.contexts]
            if len(sp) != len(fields["spaces"]):
                raise ValueError("unknown space in spaces")
            fields["spaces"] = sp
        for k in ("paused", "revoked"):
            if k in fields:
                fields[k] = bool(fields[k])
        with self._locked() as d:
            if key not in d["apps"]:
                raise ValueError(f"unknown app {key!r}")
            d["apps"][key].update(fields)
            return dict(d["apps"][key])

    def set_default_space(self, space: str) -> None:
        if space not in self.contexts:
            raise ValueError(f"unknown space {space!r}")
        with self._locked() as d:
            d["default_space"] = space
