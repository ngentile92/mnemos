"""Welcome screen (local mode, first run): one field (your name) and one button.

The name becomes the `owner` of contexts.yaml, which every connected AI already reads in its instructions, so a new
AI knows who it is talking to from the first message. The spaces are the ones the home was created with (by default
Personal and Work); they are shown, not asked. People who already have notes never see the welcome.

    <home>/data/welcome.json   {"done": true}
"""

from __future__ import annotations

import json
from pathlib import Path

from . import local_spaces as lsp

PLACEHOLDERS = {"the owner", "me"}  # contexts.py default and `mnemos-local init` default


def _file(home: Path) -> Path:
    return Path(home) / "data" / "welcome.json"


def owner(home: Path) -> str:
    try:
        o = str(lsp._load(home).get("owner") or "").strip()
    except (OSError, ValueError):
        return ""
    return "" if o.lower() in PLACEHOLDERS else o


def done(home: Path) -> bool:
    try:
        return bool(json.loads(_file(home).read_text()).get("done"))
    except (OSError, ValueError, AttributeError):
        return False


def status(home: Path) -> dict:
    try:
        spaces = [{"name": s["name"], "label": s["label"], "color": s["color"]} for s in lsp.read(home)["spaces"]]
    except (OSError, ValueError):
        spaces = []
    return {"done": done(home), "name": owner(home), "spaces": spaces}


def finish(home: Path, name: str | None = None) -> dict:
    """Save the name (optional, at most 60 characters) and mark the welcome as seen."""
    name = " ".join(str(name or "").split())
    if len(name) > 60:
        raise ValueError("Use at most 60 characters for your name.")
    if name and name != owner(home):
        data = lsp._load(home)
        data["owner"] = name
        lsp._write(home, data)
    p = _file(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"done": True}))
    return status(home)
