"""Hook entry point for AI coding apps (installed by local_hooks; never blocks, always exits 0).

  python -m hub_gateway.hook --home <Mnemos home> --app claude_code|cursor|codex --event prompt|start

prompt: bank the user's prompt (local_spool), print nothing (Cursor gets {"continue": true}).
start:  print the profile of the app's default space so the session starts knowing the user
        (Claude Code / Codex: plain text; Cursor: {"additional_context": ...}).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROFILE_MAX = 4000


KEYS = {"claude_code": "claude-code", "cursor": "cursor", "codex": "codex"}  # local_apps keys


def _app_rec(home: Path, app: str) -> tuple[dict, dict]:
    try:
        d = json.loads((home / "data" / "apps.json").read_text())
    except (OSError, ValueError):
        return {}, {}
    return d, ((d.get("apps") or {}).get(KEYS.get(app, app)) or {})


def allowed(home: Path, app: str) -> bool:
    """Paused or removed in Apps → the hook does nothing (the user's switch wins)."""
    _d, rec = _app_rec(home, app)
    return not (rec.get("paused") or rec.get("revoked"))


def profile_text(home: Path, app: str) -> str:
    """The cached profile the hub writes (<home>/profile/<space>.md) for this app's default space."""
    d, rec = _app_rec(home, app)
    space = rec.get("default") or d.get("default_space") or ""
    pdir = home / "profile"
    if not pdir.is_dir():
        return ""
    for p in ([pdir / f"{space}.md"] if space else []) + sorted(pdir.glob("*.md")):
        try:
            t = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if t:
            return t[:PROFILE_MAX]
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--event", required=True, choices=("prompt", "start"))
    a = ap.parse_args(argv)
    home = Path(a.home).expanduser()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    try:
        if not allowed(home, a.app):
            raise PermissionError
        if a.event == "prompt":
            from .local_spool import bank
            sess = str(data.get("session_id") or data.get("conversation_id") or "")
            bank(home, a.app, sess, str(data.get("prompt") or ""), str(data.get("cwd") or ""))
            if a.app == "cursor":
                print(json.dumps({"continue": True}))
        else:
            txt = profile_text(home, a.app)
            if txt:
                txt = "What Mnemos (the user's memory) knows about them:\n" + txt
                print(json.dumps({"additional_context": txt}) if a.app == "cursor" else txt)
    except Exception:  # noqa: BLE001 — a hook must never break the app
        if a.app == "cursor" and a.event == "prompt":
            print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
