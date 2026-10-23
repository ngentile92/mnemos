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

from .local_engine import embed_as

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
SAME = ("Two notes from someone's personal memory. Do they state the SAME fact (maybe in other words or another "
        'language)? Reply JSON only: {"same": true|false}\nA: ')
SEM_NEAR, SEM_RELATED = 0.80, 0.60  # on "sim" vectors (local_engine.PROMPTS): same fact / same topic


def llm_judge(llm: Any) -> Callable[[str, str], Awaitable[str | None]]:
    """A judge from an engine LLM (Built-in, Ollama or API): None = they do not contradict."""
    def yes(j: dict) -> bool:
        c = j.get("contradict")
        return c is True or str(c).lower() == "true"

    async def judge(a: str, b: str) -> str | None:
        j = await llm.json(JUDGE + a[:800] + "\nB: " + b[:800])
        if not yes(j):
            return None
        if getattr(llm, "kind", "") == "builtin" and not yes(await llm.json(JUDGE + b[:800] + "\nB: " + a[:800])):
            return None  # small built-in model: both orders must agree (halves its false alarms on paraphrases)
        if yes({"contradict": (await llm.json(SAME + a[:800] + "\nB: " + b[:800])).get("same")}):
            return None  # a paraphrase or translation ("drinks coffee" / "toma café") is not a contradiction
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
              judge: Any = "auto", embedder: Any = "auto", owner: str | None = None,
              spaces: list[dict] | None = None, health: bool = True, why: str = "schedule") -> dict:
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
    health_llm = None
    if health:
        from . import local_engine
        try:
            health_llm = await asyncio.to_thread(local_engine.get(home).llm, "hygiene")
        except Exception:  # noqa: BLE001 — rules only
            health_llm = None
        if owner is None:
            owner = _owner(home)
        if spaces is None:
            from .local_route import spaces_of
            spaces = spaces_of(home, {c: None for c in contexts})
    for ctx in contexts:
        notes = (await call(ctx, "memory_list", {"limit": 500})).get("items") or []
        vecs = None
        if embedder:
            live = [n for n in notes if n.get("editable", True) and not n.get("obsolete")][:500]
            try:
                vecs = dict(zip([n["id"] for n in live], await embed_as(embedder, [n["text"] for n in live], "sim")))
            except Exception as exc:  # noqa: BLE001 — words only
                log.warning("hygiene embeddings unavailable: %s", type(exc).__name__)
        found, related = analyze_notes(ctx, notes, today, vecs)
        if health:
            from . import local_health as hl
            checked, rest = hl.check_notes(ctx, notes, owner)
            busy = {x["notes"][0]["id"] for x in checked if x["type"] == "junk"}
            found = checked + [i for i in found if not busy & {x["id"] for x in i["notes"]}]
            found += await hl.check_moves(ctx, rest, spaces or [], health_llm)
            found += await hl.check_wrong(ctx, rest, owner or "", health_llm)
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
    rep = {"date": today.isoformat(), "mode": mode, "items": items, "why": why,
           "at": dt.datetime.now().isoformat(timespec="seconds")}
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
    from . import local_health as hl
    counts: dict[str, int] = {}
    for i in todo:
        counts[i["type"]] = counts.get(i["type"], 0) + 1
    last = next((b for b in reversed(hl.batches(home)) if not b.get("undone")), None)
    return {"items": todo, "running": running(), "date": rep.get("date"), "at": rep.get("at"), "why": rep.get("why"), "mode": rep.get("mode"),
            "total": len(items), "counts": counts,
            "last_batch": {k: last[k] for k in ("id", "at", "count", "action")} if last else None}


def _owner(home: Path) -> str:
    try:
        import yaml
        return str((yaml.safe_load((Path(home) / "contexts.yaml").read_text(encoding="utf-8")) or {}).get("owner") or "")
    except Exception:  # noqa: BLE001
        return ""


async def _apply(item: dict, action: str, call: Call, move: Any) -> list[dict]:
    """Apply one item → the undo steps for it."""
    ctx, n = item["context"], item["notes"]
    t, ops = item["type"], []
    if t == "merge" and action == "accept":
        await call(ctx, "memory_update", {"id": n[0]["id"], "text": item["proposed_text"]})
        ops.append({"op": "undo", "ctx": ctx, "id": n[0]["id"]})
        await call(ctx, "memory_delete", {"id": n[1]["id"]})
        ops.append({"op": "undo", "ctx": ctx, "id": n[1]["id"]})
    elif t == "rewrite" and action == "accept":
        await call(ctx, "memory_update", {"id": n[0]["id"], "text": item["proposed_text"]})
        ops.append({"op": "undo", "ctx": ctx, "id": n[0]["id"]})
    elif t in ("junk", "wrong") and action == "accept":
        await call(ctx, "memory_delete", {"id": n[0]["id"]})
        ops.append({"op": "undo", "ctx": ctx, "id": n[0]["id"]})
    elif (t == "stale" and action == "accept") or (t == "wrong" and action == "obsolete"):
        await call(ctx, "memory_mark_obsolete", {"id": n[0]["id"], "obsolete": True, "reason": f"cleanup: {t}"})
        ops.append({"op": "unobsolete", "ctx": ctx, "id": n[0]["id"]})
    elif t == "contradiction" and action in ("keep_newer", "keep_older"):
        loser = n[0] if action == "keep_newer" else n[1]
        await call(ctx, "memory_mark_obsolete", {"id": loser["id"], "obsolete": True, "reason": "cleanup: contradicted"})
        ops.append({"op": "unobsolete", "ctx": ctx, "id": loser["id"]})
    elif t == "move" and action == "accept":
        if move is None:
            raise ValueError("moving needs the app")
        r = await move({"context": ctx, "to": item["to"], "id": n[0]["id"], "text": n[0]["text"], "confirm": True})
        new_id = str(((r or {}).get("saved") or {}).get("id") or "")
        ops.append({"op": "undo", "ctx": ctx, "id": n[0]["id"]})
        if new_id:
            ops.append({"op": "delete", "ctx": item["to"], "id": new_id})
    else:
        raise ValueError(f"unknown action {action!r}")
    return ops


async def act(home: Path, body: dict, call: Call, move: Any = None) -> dict:
    """One item ({key, action}) or many ({keys: [...]} or {type: "junk"}, action "accept"). Applying anything takes
    a backup first and is recorded as one batch, so undo_batch can revert all of it."""
    from . import local_health as hl
    action = str(body.get("action", ""))
    items = _read(home / "data" / "hygiene.json").get("items") or []
    st = _read(home / "data" / "hygiene-state.json")
    if body.get("keys") or body.get("type"):
        keys = {str(k) for k in body.get("keys") or []}
        typ = str(body.get("type") or "")
        todo = [i for i in items if i["key"] not in st and ((keys and i["key"] in keys) or (typ and i["type"] == typ))]
        if action != "dismiss" and any(action not in {a["id"] for a in i["actions"]} for i in todo):
            raise ValueError("that action does not apply to every selected item")
    else:
        key = str(body.get("key", ""))
        todo = [i for i in items if i["key"] == key]
        if not todo:
            raise ValueError("unknown cleanup item")
    if not todo:
        return {"ok": True, "done": 0, "failed": 0}
    out: dict[str, Any] = {"ok": True, "done": 0, "failed": 0, "errors": []}
    if action != "dismiss":
        out["backup"] = await asyncio.to_thread(hl.backup, home)
        out["batch"] = hl.new_batch_id()
    ops: list[dict] = []
    for it in todo:
        try:
            if action != "dismiss":
                ops += await _apply(it, action, call, move)
            st[it["key"]] = action
            out["done"] += 1
        except ValueError:
            if len(todo) == 1:
                raise
            out["failed"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad note never stops a bulk accept
            out["failed"] += 1
            out["errors"].append(f"{it['type']}: {type(exc).__name__}")
    _write(home / "data" / "hygiene-state.json", st)
    if ops:
        hl.record(home, {"id": out["batch"], "at": dt.datetime.now().isoformat(timespec="seconds"),
                         "count": out["done"], "action": action, "backup": out["backup"], "ops": ops,
                         "keys": [i["key"] for i in todo]})
    if len(todo) == 1 and out["failed"]:
        raise RuntimeError(out["errors"][0] if out["errors"] else "not done")
    return out


async def undo_batch(home: Path, batch_id: str, call: Call) -> dict:
    """Revert every change of one apply, newest first; the items show up for review again."""
    from . import local_health as hl
    b = next((x for x in hl.batches(home) if x.get("id") == batch_id), None)
    if not b or b.get("undone"):
        raise ValueError("unknown or already undone batch")
    done = failed = 0
    for op in reversed(b["ops"]):
        try:
            if op["op"] == "undo":
                await call(op["ctx"], "memory_undo", {"id": op["id"]})
            elif op["op"] == "unobsolete":
                await call(op["ctx"], "memory_mark_obsolete", {"id": op["id"], "obsolete": False})
            elif op["op"] == "delete":
                await call(op["ctx"], "memory_delete", {"id": op["id"]})
            done += 1
        except Exception:  # noqa: BLE001
            failed += 1
    st = _read(home / "data" / "hygiene-state.json")
    for k in b.get("keys") or []:
        st.pop(k, None)
    _write(home / "data" / "hygiene-state.json", st)
    all_ = hl.batches(home)
    for x in all_:
        if x.get("id") == batch_id:
            x["undone"] = True
    p = home / "data" / "health-batches.json"
    p.write_text(json.dumps(all_, ensure_ascii=False))
    return {"ok": True, "reverted": done, "failed": failed, "backup": b.get("backup")}


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


_job: dict[str, Any] = {"task": None}


def kick(home: Path, contexts: list[str], call: Call, why: str = "manual") -> bool:
    """Start a check now in the background (after an import, or "Check now"); False if one is already running."""
    t = _job.get("task")
    if t is not None and not t.done():
        return False

    async def _go() -> None:
        try:
            await run(home, contexts, call, why=why)
        except Exception as exc:  # noqa: BLE001
            log.warning("memory health check failed: %s", type(exc).__name__)
    _job["task"] = asyncio.create_task(_go())
    return True


def running() -> bool:
    t = _job.get("task")
    return bool(t is not None and not t.done())
