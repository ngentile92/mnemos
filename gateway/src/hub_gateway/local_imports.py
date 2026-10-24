"""What was already imported, per source (0.17): so Import can say "Imported ✓ on <date>, N facts" or "3 new files
since then" instead of leaving people to guess whether something is missing.

<home>/data/imports.json:
    {"sources": {"<source id>": {"label", "kind", "last_import", "saved", "skipped", "files": {path: mtime}}},
     "history": [{"batch", "at", "saved", "skipped", "sources": [labels], "undone": bool}]}
Only paths and modification times are kept, never file contents.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

MAX_HISTORY = 50


def _path(home: Path) -> Path:
    return Path(home) / "data" / "imports.json"


def load(home: Path) -> dict[str, Any]:
    try:
        d = json.loads(_path(home).read_text())
        if isinstance(d, dict):
            d.setdefault("sources", {})
            d.setdefault("history", [])
            return d
    except (OSError, ValueError):
        pass
    return {"sources": {}, "history": []}


def _save(home: Path, d: dict) -> None:
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


def _mtimes(paths: list[str]) -> dict[str, float]:
    out = {}
    for p in paths:
        try:
            out[p] = round(Path(p).stat().st_mtime, 3)
        except OSError:
            pass
    return out


def status(rec: dict | None, paths: list[str]) -> dict[str, Any]:
    """imported (nothing new) · changed (n new or edited files since) · new (never imported)."""
    if not rec:
        return {"status": "new", "new_files": len(paths)}
    seen = rec.get("files") or {}
    now = _mtimes(paths)
    fresh = sum(1 for p, m in now.items() if p not in seen or m > float(seen[p]) + 0.001)
    return {"status": "changed" if fresh else "imported", "new_files": fresh, "last_import": rec.get("last_import"),
            "saved": int(rec.get("saved", 0))}


def annotate(home: Path, sources: list[dict]) -> list[dict]:
    d = load(home)
    return [{**s, **status(d["sources"].get(s["id"]), list(s.get("paths") or []))} for s in sources]


def record(home: Path, sources: list[dict], saved_by: dict[str, int], skipped_by: dict[str, int], batch: str) -> None:
    """After a commit: every previewed source counts as imported now (even when it had nothing new)."""
    if not sources:
        return
    d = load(home)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for s in sources:
        r = d["sources"].setdefault(s["id"], {"saved": 0, "skipped": 0})
        r.update({"label": s.get("label"), "kind": s.get("kind"), "last_import": now,
                  "files": _mtimes(list(s.get("paths") or []))})
        r["saved"] = int(r.get("saved", 0)) + saved_by.get(s["id"], 0)
        r["skipped"] = int(r.get("skipped", 0)) + skipped_by.get(s["id"], 0)
    d["history"].insert(0, {"batch": batch, "at": now, "saved": sum(saved_by.values()),
                            "skipped": sum(skipped_by.values()), "sources": [s.get("label") for s in sources],
                            "undone": False})
    d["history"] = d["history"][:MAX_HISTORY]
    _save(home, d)


def mark_undone(home: Path, batch: str, removed: int) -> None:
    d = load(home)
    for h in d["history"]:
        if h.get("batch") == batch:
            h["undone"], h["removed"] = True, removed
    _save(home, d)


def summary(sources: list[dict]) -> dict[str, Any]:
    todo = [s for s in sources if s.get("status") in ("new", "changed")]
    done = [s for s in sources if s.get("status") == "imported"]
    return {"up_to_date": not todo, "todo": len(todo), "imported": len(done),
            "saved": sum(int(s.get("saved") or 0) for s in sources)}
