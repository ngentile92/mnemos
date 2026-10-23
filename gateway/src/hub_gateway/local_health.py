"""Memory health (0.14): per-note checks that turn existing memory into one-click proposals, plus a backup before
anything is applied and an undo for a whole batch. Generic rules only: nothing here knows a user, a company or a
project; the owner's name and the spaces come from the user's own config (contexts.yaml).

Checks, each → one item in the same inbox format as local_hygiene (the Memory screen shows them):
  junk     not a durable fact about the person: code, tables, paths, logs, assistant narration (memfilter rules)
  rewrite  written about the owner in the third person ("The user prefers…", "<Name> utiliza…") → first-person-neutral
  move     clearly about another space (local model if there is one, else the keyword rules); only when confident
  wrong    likely a mistake: about the assistant, a fictional character or a sample (needs the local model)
Duplicates, contradictions and stale notes stay in local_hygiene.analyze_notes.

Nothing is changed by a check. Applying (one item or a bulk "accept all of this kind") first copies the memory
files to <home>/data/backups/health-<ts>/ (last 5 kept) and records what it did, so "Undo batch" can put it back.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import memfilter as mf

log = logging.getLogger("hub.local_health")
MOVE_MIN_CONF = 0.7
MAX_WRONG_CHECK = 80
KEEP_BACKUPS = 5
# memfilter reasons that mean "this is not a fact about the person" (sensitive/length are the user's call)
JUNK_REASONS = {"url", "localhost/port", "path", "file", "stack trace", "code", "hash/id", "version pin",
                "branch/commit", "size/timing", "table/schema", "assistant", "request", "ack", "task log",
                "one-off ask", "worked", "artifact", "color/code", "pasted", "generic", "encyclopedic", "secret"}
JUNK_LABEL = {"secret": "looks like a password or key", "code": "looks like code", "table/schema": "looks like a table or schema",
              "stack trace": "looks like an error log", "assistant": "describes the assistant, not you",
              "ack": "just an acknowledgement", "task log": "a one-off task step"}
WRONG = ("A note from someone's personal memory (the owner is {owner}). Is it LIKELY WRONG to keep as a fact about "
         "the owner: about the AI assistant, a fictional character or role-play, example/sample data, or plainly "
         'about another person as if it were the owner? Reply JSON only: {{"wrong": true|false, "why": "<short>"}}\n'
         "Note: ")


def _key(kind: str, nid: str, extra: str = "") -> str:
    return hashlib.sha1(f"{kind}:{nid}:{extra}".encode()).hexdigest()[:16]


def _note(n: dict) -> dict:
    return {"id": n["id"], "text": n.get("text", ""), "created_at": n.get("created_at"), "source_app": n.get("source_app")}


def junk_reason(text: str) -> str | None:
    t = (text or "").strip()
    if (blk := mf.pasted_block(t)):
        return "table/schema" if blk == "table" else "stack trace" if blk == "log" else "code"
    if len(t) > 300:  # long notes: judge the start (a pasted block shows itself early)
        t = t[:300]
    c = mf.classify(t if len(t) >= 10 else t + " " * (10 - len(t)))
    r = c["reason"]
    if r in JUNK_REASONS:
        return r
    if mf.vague(t):
        return "vague"
    return None


def check_notes(ctx: str, notes: list[dict], owner: str | None = None) -> tuple[list[dict], list[dict]]:
    """Rule checks for one space → (items, notes left for the move/wrong checks)."""
    out, rest = [], []
    live = [n for n in notes if n.get("editable", True) and not n.get("obsolete") and n.get("text")]
    for n in live:
        t = str(n["text"])
        if (r := junk_reason(t)):
            out.append({"key": _key("junk", n["id"]), "type": "junk", "context": ctx,
                        "title": "Not a fact about you", "explain": "It " + JUNK_LABEL.get(r, f"looks like a detail of a task ({r})")
                        + ". Forgetting it keeps answers focused (undoable).", "reason": r, "notes": [_note(n)],
                        "actions": [{"id": "accept", "label": "Forget", "kind": "primary"}]})
            continue
        fixed = mf.tidy(t, owner)
        if fixed and fixed != t.strip():
            out.append({"key": _key("rewrite", n["id"], fixed), "type": "rewrite", "context": ctx,
                        "title": "Written about you in the third person",
                        "explain": "Same fact, phrased the way Mnemos stores memories about you.",
                        "notes": [_note(n)], "proposed_text": fixed,
                        "actions": [{"id": "accept", "label": "Rewrite", "kind": "primary"}]})
        rest.append(n)
    return out, rest


async def check_moves(ctx: str, notes: list[dict], spaces: list[dict], llm: Any = None) -> list[dict]:
    """Notes that clearly belong to another space. Never proposes shared, never when unsure."""
    from .local_route import route_many
    if len([s for s in spaces if s.get("name") != "shared"]) < 2 or not notes:
        return []
    names = {s["name"] for s in spaces}
    if ctx not in names:
        return []
    got = await route_many([str(n["text"]) for n in notes], spaces, ctx, llm=llm)
    out = []
    for n, r in zip(notes, got):
        to = r.get("space")
        if to and to != ctx and to != "shared" and not r.get("review") and float(r.get("confidence") or 0) >= MOVE_MIN_CONF:
            out.append({"key": _key("move", n["id"], to), "type": "move", "context": ctx, "to": to,
                        "title": f"Belongs in {to}?", "explain": (r.get("reason") or "it is about that space")
                        + f" ({'local model' if r.get('by') == 'model' else 'keyword rules'}, {int(100 * float(r['confidence']))}% sure).",
                        "notes": [_note(n)], "actions": [{"id": "accept", "label": f"Move to {to}", "kind": "primary"}]})
    return out


async def check_wrong(ctx: str, notes: list[dict], owner: str, llm: Any) -> list[dict]:
    if not llm:
        return []
    out = []
    for n in notes[:MAX_WRONG_CHECK]:
        try:
            j = await llm.json(WRONG.format(owner=owner or "the user") + str(n["text"])[:600])
        except Exception:  # noqa: BLE001 — model down: skip this check
            return out
        if isinstance(j, dict) and j.get("wrong") in (True, "true", "yes"):
            out.append({"key": _key("wrong", n["id"]), "type": "wrong", "context": ctx, "title": "Probably not about you",
                        "explain": str(j.get("why") or "It reads like a mistake.")[:160], "notes": [_note(n)],
                        "actions": [{"id": "accept", "label": "Forget", "kind": "primary"},
                                    {"id": "obsolete", "label": "Keep, hide from answers", "kind": ""}]})
    return out


# ------------------------------------------------------------------ backup + batch undo
def backup(home: Path) -> str:
    """Copy the memory files (notes, per-space model/graph/search, Markdown memory) before applying changes."""
    home = Path(home)
    dst = home / "data" / "backups" / f"health-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    dst.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = home / "data"
    for src in list((data / "notes").glob("*.sqlite")) + [p for p in data.glob("*/*.sqlite")
                                                         if p.parent.name not in ("notes", "backups")]:
        rel = src.relative_to(data)
        (dst / rel.parent).mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(src) as a, sqlite3.connect(dst / rel) as b:
            a.backup(b)
    if (home / "memory").is_dir():
        shutil.copytree(home / "memory", dst / "memory", dirs_exist_ok=True)
    olds = sorted((data / "backups").glob("health-*"))
    for old in olds[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return str(dst)


def _batches(home: Path) -> Path:
    return Path(home) / "data" / "health-batches.json"


def record(home: Path, batch: dict) -> None:
    p = _batches(home)
    try:
        all_ = json.loads(p.read_text())
    except (OSError, ValueError):
        all_ = []
    all_ = (all_ + [batch])[-20:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(all_, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(p)


def batches(home: Path) -> list[dict]:
    try:
        return json.loads(_batches(home).read_text())
    except (OSError, ValueError):
        return []


def new_batch_id() -> str:
    return "hb-" + hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]
