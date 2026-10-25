"""First-run timing, kept on this computer only (FTUE #200). Nothing is ever sent anywhere.

    <home>/data/ftue.json   {"welcome": "<iso>", "first_app": "<iso>", "first_save": "<iso>", "aha": "<iso>", "skipped": "<iso>"}

Each step is stamped once (the first time). `summary` gives minutes from the welcome to each step, so a friend test
can read "time to aha" from Settings without any telemetry.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

STEPS = ("welcome", "first_app", "first_save", "aha", "skipped")  # aha: since v0.18, the map at the end of the first run


def _file(home: Path) -> Path:
    return Path(home) / "data" / "ftue.json"


def load(home: Path) -> dict:
    try:
        d = json.loads(_file(home).read_text())
        return {k: v for k, v in d.items() if k in STEPS} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def mark(home: Path, step: str, now: dt.datetime | None = None) -> dict:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}")
    d = load(home)
    if step not in d:
        d[step] = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
        p = _file(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=1))
    return summary(home)


def summary(home: Path) -> dict:
    d = load(home)
    out: dict = {"steps": d, "minutes": {}}
    if "welcome" in d:
        t0 = dt.datetime.fromisoformat(d["welcome"])
        for k, v in d.items():
            if k != "welcome":
                out["minutes"][k] = round((dt.datetime.fromisoformat(v) - t0).total_seconds() / 60, 1)
    return out
