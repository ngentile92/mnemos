"""Labs (local mode, 0.11): optional layers on top of the simple memory, off by default and kept maintained.

Labs = the layered memory model's screens (entities, profile built from entities, "still true?" digest, Settings →
Memory model), the Graph view and the nightly consolidation. The simple memory (Markdown files, see memfiles.py) does
not need any of them. Turn Labs on in Settings → Labs, or with MNEMOS_LABS=1 (wins over the setting).

    <home>/data/labs.json   {"on": true|false}
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _file(home: Path) -> Path:
    return Path(home) / "data" / "labs.json"


def forced() -> bool | None:
    v = os.environ.get("MNEMOS_LABS", "").strip().lower()
    if v in ("1", "on", "true", "yes"):
        return True
    if v in ("0", "off", "false", "no"):
        return False
    return None


def enabled(home: Path) -> bool:
    f = forced()
    if f is not None:
        return f
    try:
        return bool(json.loads(_file(home).read_text()).get("on"))
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(home: Path, on: bool) -> dict:
    p = _file(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"on": bool(on)}))
    return status(home)


def status(home: Path) -> dict:
    return {"on": enabled(home), "forced": forced() is not None}
