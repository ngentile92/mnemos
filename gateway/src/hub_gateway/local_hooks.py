"""Install / remove Mnemos' capture hooks in AI coding apps (local mode, opt-in from Apps → Auto-capture).

  Claude Code  ~/.claude/settings.json   "hooks": UserPromptSubmit + SessionStart
  Codex        ~/.codex/hooks.json       same schema (Codex asks you to trust new hooks once: /hooks)
  Cursor       ~/.cursor/hooks.json      {"version": 1, "hooks": beforeSubmitPrompt + sessionStart}

Each hook runs `python -m hub_gateway.hook` with this Python, banks the user's prompt to the local spool (no
model, <1 s, never blocks) and, at session start, hands the app the user's profile. Every edit keeps the user's
other settings, writes a one-time backup (<file>.mnemos-backup) and is idempotent; removing deletes only our entries.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

MARK = "hub_gateway.hook"
LABELS = {"claude_code": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


def _file(app: str, user_home: Path) -> Path:
    return {"claude_code": user_home / ".claude" / "settings.json", "codex": user_home / ".codex" / "hooks.json",
            "cursor": user_home / ".cursor" / "hooks.json"}[app]


def _cmd(app: str, event: str, home: Path, python: str | None = None) -> str:
    py = python or sys.executable
    return " ".join(shlex.quote(x) for x in (py, "-m", MARK, "--home", str(home), "--app", app, "--event", event))


def _load(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = p.with_name(p.name + ".mnemos-backup")
    if p.exists() and not bak.exists():
        bak.write_bytes(p.read_bytes())
    tmp = p.with_name(p.name + ".mnemos-tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    os.replace(tmp, p)


def _strip(entries: list) -> list:
    """Remove our handlers from a hook event list, keeping everything else."""
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            out.append(e)
            continue
        if MARK in str(e.get("command", "")):
            continue
        if isinstance(e.get("hooks"), list):
            hs = [h for h in e["hooks"] if MARK not in str((h or {}).get("command", ""))]
            if not hs:
                continue
            e = {**e, "hooks": hs}
        out.append(e)
    return out


def _events(app: str) -> dict[str, str]:
    return ({"beforeSubmitPrompt": "prompt", "sessionStart": "start"} if app == "cursor"
            else {"UserPromptSubmit": "prompt", "SessionStart": "start"})


def status(user_home: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for app, label in LABELS.items():
        p = _file(app, user_home)
        d = _load(p)
        on = MARK in json.dumps(d.get("hooks") or {})
        present = p.parent.is_dir()
        out[app] = {"label": label, "installed": on, "app_found": present, "file": str(p).replace(str(user_home), "~")}
    return out


def install(app: str, home: Path, user_home: Path, python: str | None = None) -> dict[str, Any]:
    if app not in LABELS:
        raise ValueError("unknown app")
    p = _file(app, user_home)
    d = _load(p)
    hooks = d.get("hooks") if isinstance(d.get("hooks"), dict) else {}
    for ev, kind in _events(app).items():
        lst = _strip(hooks.get(ev) or [])
        cmd = _cmd(app, kind, home, python)
        if app == "cursor":
            lst.append({"command": cmd, "timeout": 5})
        else:
            lst.append({"hooks": [{"type": "command", "command": cmd, "timeout": 5}]})
        hooks[ev] = lst
    d["hooks"] = hooks
    if app == "cursor":
        d.setdefault("version", 1)
    _write(p, d)
    return status(user_home)[app]


def uninstall(app: str, user_home: Path) -> dict[str, Any]:
    if app not in LABELS:
        raise ValueError("unknown app")
    p = _file(app, user_home)
    d = _load(p)
    if isinstance(d.get("hooks"), dict):
        hooks = {k: _strip(v) for k, v in d["hooks"].items()}
        d["hooks"] = {k: v for k, v in hooks.items() if v}
        _write(p, d)
    return status(user_home)[app]
