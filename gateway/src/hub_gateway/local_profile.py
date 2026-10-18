"""Profile per space (local mode, v0.8): the short text every AI gets first (memory_profile tool, the SessionStart
hook of coding apps). Built from the space's notes, most important first:

  1. pinned notes (the user said "this matters")
  2. who the person is / what they prefer / people / goals (notes tagged kind-identity, kind-preference, ...)
  3. projects and decisions (kind-project_state, kind-decision)
  4. the newest other notes, while there is room

Expired / out-of-date notes are left out. Cached in <home>/profile/<space>.md so a hook can read it without
asking the server. v0.9 replaces this with the consolidated L0 profile.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_CHARS = 2400
SECTIONS = (("About", ("kind-identity", "kind-relationship")), ("Preferences", ("kind-preference",)),
            ("Goals", ("kind-goal",)), ("Projects and decisions", ("kind-project_state", "kind-decision", "kind-skill_fact")))


def build(items: list[dict[str, Any]], limit: int = MAX_CHARS) -> str:
    live = [i for i in items if not i.get("obsolete") and i.get("validity") != "expired" and str(i.get("text", "")).strip()]
    used: set[str] = set()
    out: list[str] = []
    size = 0

    def take(title: str, rows: list[dict]) -> None:
        nonlocal size
        lines = []
        for r in rows:
            t = " ".join(str(r["text"]).split())[:300]
            if r.get("id") in used or size + len(t) > limit:
                continue
            used.add(r.get("id"))
            lines.append(f"- {t}")
            size += len(t) + 3
        if lines:
            out.append(f"## {title}\n" + "\n".join(lines))

    take("Pinned", [i for i in live if i.get("pinned")])
    for title, tags in SECTIONS:
        take(title, [i for i in live if set(i.get("tags") or []) & set(tags)])
    take("Recent", live)
    return "\n\n".join(out)


def write_cache(home: Path, space: str, text: str) -> None:
    d = Path(home) / "profile"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = d / f".{space}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, d / f"{space}.md")
