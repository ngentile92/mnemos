"""Live capture, fast lane: what AI coding apps' hooks hand us is banked raw, with no model, in well under a second.

  <home>/spool/<app>/<session>.jsonl   one line per user turn: {"ts", "text", "cwd"} (0600)

Only the person's own prompts are banked, cleaned the same way as mining (memfilter.clean_message: no code blocks,
pasted logs, quotes, acks or slash commands; >8 KB pastes dropped) and never anything that looks like a secret.
The capture worker (local_capture) reads a session once it has gone quiet and turns it into memory.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

APPS = ("claude_code", "cursor", "codex")
MAX_TURNS = 400  # per session file


def _sid(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "nosession")[:80] or "nosession"


def spool_dir(home: Path) -> Path:
    return Path(home) / "spool"


def bank(home: Path, app: str, session: str, text: str, cwd: str = "") -> bool:
    """Append one cleaned user turn. Returns False when there was nothing worth keeping."""
    from .local_discover import has_secret
    from .memfilter import clean_message
    if app not in APPS:
        return False
    t = clean_message(text or "")
    if not t or has_secret(t):
        return False
    d = spool_dir(home) / app
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / f"{_sid(session)}.jsonl"
    line = json.dumps({"ts": time.time(), "text": t, "cwd": (cwd or "")[:300]}, ensure_ascii=False) + "\n"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return True


def sessions(home: Path) -> list[dict[str, Any]]:
    """Every banked session: app, id, path, turns, last write time."""
    out = []
    root = spool_dir(home)
    for app in APPS:
        for p in sorted((root / app).glob("*.jsonl")) if (root / app).is_dir() else []:
            try:
                st = p.stat()
                n = sum(1 for _ in p.open(encoding="utf-8"))
            except OSError:
                continue
            out.append({"app": app, "session": p.stem, "path": str(p), "turns": min(n, MAX_TURNS), "mtime": st.st_mtime})
    return out


def read(path: str | Path) -> list[dict[str, Any]]:
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines()[-MAX_TURNS:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def counts(home: Path) -> dict[str, int]:
    c: dict[str, int] = {}
    for s in sessions(home):
        c[s["app"]] = c.get(s["app"], 0) + s["turns"]
    return c
