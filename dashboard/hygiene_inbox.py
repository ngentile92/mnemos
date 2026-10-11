"""Hygiene inbox: turns the latest nightly report (scripts/memory_hygiene.py → ~/mnemos-hygiene/*.json) into
items you accept or dismiss one by one in the Memory tab. Accepting runs the same memory tools an assistant
would (memory_update / memory_delete / memory_mark_obsolete), so every change is versioned and undoable.
Dismissed/accepted items are remembered in dev/state/hygiene-inbox.json so they do not come back."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

MAX_STALE = 30


def report_dir(env: dict[str, str]) -> Path:
    return Path(os.environ.get("MNEMOS_HYGIENE_DIR") or env.get("MNEMOS_HYGIENE_DIR") or Path.home() / "mnemos-hygiene")


def latest(d: Path) -> dict[str, Any] | None:
    files = sorted(d.glob("hygiene-*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return None


def _key(kind: str, *ids: str) -> str:
    return hashlib.sha1(f"{kind}:{':'.join(ids)}".encode()).hexdigest()[:16]


def build(report: dict[str, Any], ds2ctx: dict[str, str]) -> list[dict[str, Any]]:
    """Items, newest report only. Notes in datasets no context owns (shared) are skipped: not editable here."""
    notes = {n["id"]: n for n in report.get("notes", [])}
    f = report.get("findings") or {}
    checks = report.get("checks") or {}
    out: list[dict[str, Any]] = []

    def note(i: str) -> dict[str, Any]:
        n = notes.get(i) or {}
        return {"id": i, "text": n.get("text", ""), "created_at": n.get("created_at"), "source_app": n.get("source_app")}

    for m in f.get("merge_proposals", []):
        ctx = ds2ctx.get(m.get("dataset", ""))
        if ctx and m.get("keep") in notes and m.get("delete") in notes:
            out.append({"key": _key("merge", m["keep"], m["delete"]), "type": "merge", "context": ctx,
                        "title": "Almost the same note twice",
                        "explain": "Keep one note with the text of both; the older copy is forgotten (undoable).",
                        "notes": [note(m["keep"]), note(m["delete"])], "proposed_text": m.get("text", ""),
                        "actions": [{"id": "accept", "label": "Merge", "kind": "primary"}]})
    for c in checks.get("contradictions", []):
        ctx = ds2ctx.get(c.get("dataset", ""))
        if ctx and c.get("older") in notes and c.get("newer") in notes:
            out.append({"key": _key("contra", c["older"], c["newer"]), "type": "contradiction", "context": ctx,
                        "title": "These notes disagree",
                        "explain": (c.get("conflict") or "The newer note says something different.")[:300],
                        "notes": [note(c["older"]), note(c["newer"])],
                        "actions": [{"id": "keep_newer", "label": "Newer is right", "kind": "primary"},
                                    {"id": "keep_older", "label": "Older is right", "kind": ""}]})
    seen: set[str] = set()
    for o in checks.get("outdated", []):
        ctx = ds2ctx.get(o.get("dataset", ""))
        if ctx and o.get("id") in notes:
            seen.add(o["id"])
            out.append({"key": _key("stale", o["id"]), "type": "stale", "context": ctx,
                        "title": "Probably out of date", "explain": str(o.get("reason", ""))[:300],
                        "notes": [note(o["id"])],
                        "actions": [{"id": "accept", "label": "Mark obsolete", "kind": "primary"}]})
    for i in (f.get("stale") or [])[:MAX_STALE]:
        n = notes.get(i)
        ctx = ds2ctx.get((n or {}).get("dataset", ""))
        if n and ctx and i not in seen:
            out.append({"key": _key("stale", i), "type": "stale", "context": ctx, "title": "Old note, still true?",
                        "explain": f"Saved {str(n.get('created_at'))[:10]} and not touched since.",
                        "notes": [note(i)],
                        "actions": [{"id": "accept", "label": "Mark obsolete", "kind": "primary"}]})
    return out


class Inbox:
    def __init__(self, state_file: Path):
        self.state_file = state_file

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_file)

    def pending(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        st = self.state()
        return [i for i in items if i["key"] not in st]

    def mark(self, key: str, how: str) -> None:
        st = self.state()
        st[key] = how
        self._save(st)


def act(item: dict[str, Any], action: str, call: Callable[[str, dict], dict]) -> list[dict[str, Any]]:
    """Run the item's action. call(action, body) = dashboard memory_action. Returns the tool results."""
    ctx, n = item["context"], item["notes"]
    if action == "dismiss":
        return []
    if item["type"] == "merge" and action == "accept":
        return [call("update", {"context": ctx, "id": n[0]["id"], "text": item["proposed_text"]}),
                call("delete", {"context": ctx, "id": n[1]["id"]})]
    if item["type"] == "stale" and action == "accept":
        return [call("obsolete", {"context": ctx, "id": n[0]["id"], "reason": "hygiene inbox: out of date"})]
    if item["type"] == "contradiction" and action in ("keep_newer", "keep_older"):
        loser = n[0] if action == "keep_newer" else n[1]
        return [call("obsolete", {"context": ctx, "id": loser["id"], "reason": "hygiene inbox: contradicted"})]
    raise ValueError(f"unknown action {action!r} for {item['type']}")
