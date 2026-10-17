"""Nightly memory cleanup for local mode (mnemos-local / Desktop): proposals only, never edits on its own.

Finds, per context and dataset: near-duplicates (token Jaccard >= 0.8, same rule as import / check-on-save) →
"merge"; related notes that may disagree → "contradiction" (asks the engine local_engine picks for "hygiene":
API if enabled, Ollama, or the built-in model; otherwise a lexical rule: same topic but different numbers or a
negation on one side). With embeddings (built-in or Ollama) "near"/"related" also count meaning, not only shared
words (SEM_NEAR / SEM_RELATED cosine); notes not touched for a
year → "stale". Items use the hub's cleanup-inbox format, so the Memory screen shows them unchanged. The user
accepts or dismisses each one; accepting goes through the normal tools (history + undo).
Report: <home>/data/hygiene.json; decisions: <home>/data/hygiene-state.json (both 0600).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from .local_import import _sentences, _tokens, norm

log = logging.getLogger("hub.local_hygiene")
NEAR, RELATED, STALE_DAYS, MAX_PAIRS, MAX_STALE = 0.8, 0.35, 365, 200, 10
_NEG = re.compile(r"\b(no|not|never|nunca|ya no|dejó|dejo|stopped|anymore|ex)\b", re.I)
_NUM = re.compile(r"\d+")

Call = Callable[[str, str, dict], Awaitable[dict]]


def _key(kind: str, *ids: str) -> str:
    return hashlib.sha1(f"{kind}:{':'.join(ids)}".encode()).hexdigest()[:16]


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return float(norm(a) == norm(b))
    return len(ta & tb) / len(ta | tb)


def lexical_conflict(a: str, b: str) -> str | None:
    if set(_NUM.findall(a)) != set(_NUM.findall(b)) and _NUM.search(a) and _NUM.search(b):
        return "Same topic, different numbers or dates."
    if bool(_NEG.search(a)) != bool(_NEG.search(b)):
        return "Same topic, but one of them negates it."
    return None


def merged(newer: str, older: str) -> str:
    have = {norm(s) for s in _sentences(newer)}
    extra = [s for s in _sentences(older) if norm(s) not in have and jaccard(s, newer) < NEAR]
    return (newer.rstrip() + (" " + " ".join(extra) if extra else ""))[:20000]


def _when(n: dict) -> str:
    return str(n.get("created_at") or "")


def _note(n: dict) -> dict:
    return {"id": n["id"], "text": n.get("text", ""), "created_at": n.get("created_at"), "source_app": n.get("source_app")}


JUDGE = ("Two notes from someone's personal memory. Do they CONTRADICT each other (cannot both be true now)? "
         'Reply JSON only: {"contradict": true|false, "why": "<short reason>"}\nA: ')
SEM_NEAR, SEM_RELATED = 0.93, 0.75


def llm_judge(llm: Any) -> Callable[[str, str], Awaitable[str | None]]:
    """A judge from an engine LLM (Built-in, Ollama or API): None = they do not contradict."""
    def yes(j: dict) -> bool:
        c = j.get("contradict")
        return c is True or str(c).lower() == "true"

    async def judge(a: str, b: str) -> str | None:
        j = await llm.json(JUDGE + a[:800] + "\nB: " + b[:800])
        if not yes(j):
            return None
        if getattr(llm, "mode", "") == "classify" and not yes(await llm.json(JUDGE + b[:800] + "\nB: " + a[:800])):
            return None  # small built-in model: both orders must agree (halves its false alarms on paraphrases)
        return str(j.get("why") or "They disagree.")[:300]
    return judge


async def ollama_judge(a: str, b: str) -> str | None:
    """Kept for callers of the old API: the env-configured Ollama (HUB_OLLAMA_URL + HUB_ANSWER_MODEL)."""
    from .local_engine import OllamaLLM
    model, url = os.environ.get("HUB_ANSWER_MODEL"), os.environ.get("HUB_OLLAMA_URL")
    if not model or not url:
        raise LookupError
    return await llm_judge(OllamaLLM(url, model))(a, b)


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = sum(x * x for x in a) ** 0.5 or 1.0, sum(x * x for x in b) ** 0.5 or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def analyze_notes(ctx: str, notes: list[dict], today: dt.date,
                  vecs: dict[str, list[float]] | None = None) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """→ (merge + stale items, related pairs to judge for contradictions). vecs: note id → embedding (optional)."""
    out, related, used = [], [], set()
    live = [n for n in notes if n.get("editable", True) and not n.get("obsolete")]
    by_ds: dict[str, list[dict]] = {}
    for n in live:
        by_ds.setdefault(str(n.get("dataset")), []).append(n)
    for group in by_ds.values():
        group.sort(key=_when, reverse=True)  # newest first
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if len(related) >= MAX_PAIRS:
                    break
                j = jaccard(a["text"], b["text"])
                cos = _cos(vecs[a["id"]], vecs[b["id"]]) if vecs and a["id"] in vecs and b["id"] in vecs else 0.0
                near = j >= NEAR or cos >= SEM_NEAR
                if near and lexical_conflict(a["text"], b["text"]):
                    related.append((b, a))
                elif near and a["id"] not in used and b["id"] not in used:
                    used |= {a["id"], b["id"]}
                    out.append({"key": _key("merge", a["id"], b["id"]), "type": "merge", "context": ctx,
                                "title": "Almost the same note twice",
                                "explain": "Keep one note with the text of both; the older copy is forgotten (undoable).",
                                "notes": [_note(a), _note(b)], "proposed_text": merged(a["text"], b["text"]),
                                "actions": [{"id": "accept", "label": "Merge", "kind": "primary"}]})
                elif j >= RELATED or cos >= SEM_RELATED:
                    related.append((b, a))  # (older, newer)
    cutoff = (today - dt.timedelta(days=STALE_DAYS)).isoformat()
    old = [n for n in live if _when(n) and _when(n) < cutoff and not n.get("pinned") and n["id"] not in used]
    for n in sorted(old, key=_when)[:MAX_STALE]:
        out.append({"key": _key("stale", n["id"]), "type": "stale", "context": ctx, "title": "Old note, still true?",
                    "explain": f"Saved {_when(n)[:10]} and not touched since.", "notes": [_note(n)],
                    "actions": [{"id": "accept", "label": "Mark obsolete", "kind": "primary"}]})
    return out, related


async def run(home: Path, contexts: list[str], call: Call, today: dt.date | None = None,
              judge: Any = "auto", embedder: Any = "auto") -> dict:
    """judge/embedder "auto" = whatever local_engine has for hygiene (None = lexical / no embeddings)."""
    today = today or dt.date.today()
    items: list[dict] = []
    llm = True
    mode = "lexical"
    if judge == "auto" or embedder == "auto":
        from . import local_engine
        eng = local_engine.get(home)
        if judge == "auto":
            e = await asyncio.to_thread(eng.llm, "hygiene")
            judge, mode = (llm_judge(e), e.name) if e else (None, "lexical")
        if embedder == "auto":
            embedder = await asyncio.to_thread(eng.embedder)
    elif judge:
        mode = "llm"
    for ctx in contexts:
        notes = (await call(ctx, "memory_list", {"limit": 500})).get("items") or []
        vecs = None
        if embedder:
            live = [n for n in notes if n.get("editable", True) and not n.get("obsolete")][:500]
            try:
                vecs = dict(zip([n["id"] for n in live], await embedder.embed([n["text"] for n in live])))
            except Exception as exc:  # noqa: BLE001 — words only
                log.warning("hygiene embeddings unavailable: %s", type(exc).__name__)
        found, related = analyze_notes(ctx, notes, today, vecs)
        for older, newer in related[:40]:
            why = None
            if llm and judge:
                try:
                    why = await judge(older["text"], newer["text"])
                except Exception:  # noqa: BLE001 — engine down: fall back to the lexical rule
                    llm, mode = False, "lexical"
            if not (llm and judge):
                why = lexical_conflict(older["text"], newer["text"])
            if why:
                found.append({"key": _key("contra", older["id"], newer["id"]), "type": "contradiction", "context": ctx,
                              "title": "These notes disagree", "explain": why, "notes": [_note(older), _note(newer)],
                              "actions": [{"id": "keep_newer", "label": "Newer is right", "kind": "primary"},
                                          {"id": "keep_older", "label": "Older is right", "kind": ""}]})
        items += found
    rep = {"date": today.isoformat(), "mode": mode, "items": items}
    _write(home / "data" / "hygiene.json", rep)
    return rep


def _write(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def pending(home: Path) -> dict:
    rep, st = _read(home / "data" / "hygiene.json"), _read(home / "data" / "hygiene-state.json")
    items = rep.get("items") or []
    todo = [i for i in items if i["key"] not in st]
    return {"items": todo, "date": rep.get("date"), "total": len(items)}


async def act(home: Path, body: dict, call: Call) -> dict:
    key, action = str(body.get("key", "")), str(body.get("action", ""))
    item = next((i for i in _read(home / "data" / "hygiene.json").get("items") or [] if i["key"] == key), None)
    if not item:
        raise ValueError("unknown cleanup item")
    ctx, n = item["context"], item["notes"]
    if action == "dismiss":
        pass
    elif item["type"] == "merge" and action == "accept":
        await call(ctx, "memory_update", {"id": n[0]["id"], "text": item["proposed_text"]})
        await call(ctx, "memory_delete", {"id": n[1]["id"]})
    elif item["type"] == "stale" and action == "accept":
        await call(ctx, "memory_mark_obsolete", {"id": n[0]["id"], "obsolete": True, "reason": "cleanup: out of date"})
    elif item["type"] == "contradiction" and action in ("keep_newer", "keep_older"):
        loser = n[0] if action == "keep_newer" else n[1]
        await call(ctx, "memory_mark_obsolete", {"id": loser["id"], "obsolete": True, "reason": "cleanup: contradicted"})
    else:
        raise ValueError(f"unknown action {action!r}")
    st = _read(home / "data" / "hygiene-state.json")
    st[key] = action
    _write(home / "data" / "hygiene-state.json", st)
    return {"ok": True}


async def nightly(home: Path, contexts: list[str], call: Call, hour: int = 3) -> None:
    """Background loop in `serve`: once a day after `hour` (or at start if the last report is older than a day)."""
    while True:
        try:
            now = dt.datetime.now()
            last = _read(home / "data" / "hygiene.json").get("date")
            stale = not last or last < (now.date() - dt.timedelta(days=1)).isoformat()
            if stale or (last < now.date().isoformat() and now.hour >= hour):
                await run(home, contexts, call)
        except Exception as exc:  # noqa: BLE001 — never take the hub down
            log.warning("local hygiene failed: %s", type(exc).__name__)
        await asyncio.sleep(3600)
