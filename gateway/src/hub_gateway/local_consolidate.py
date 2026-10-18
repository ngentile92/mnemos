"""Sleep-time consolidation (local mode, v0.9): rewrites the layered model so what the user and the AIs see stays
short, current and sourced. Never edits or deletes a note; every change is a model field logged under a job id,
so a whole run can be undone.

  expire      time-bound facts past their expiry → expired (kept, out of the profile and down in search)
  supersede   per entity, a newer fact that contradicts an older one ("now uses Tiingo" vs "uses Kaiko", "2 cats" vs
              "3 cats", "switched from X") → the older one is superseded (valid_to + superseded_by), never deleted
  decay       strength = importance · e^(−days since last use / S), S = 60 · (1 + uses) days; pinned never decays
  summaries   dirty entities get a 2–4 sentence summary; every sentence cites the facts it comes from (model when
              available, otherwise the top facts verbatim)
  profile     L0 per space: About / Preferences / People / Working on / Goals, strongest facts first
  digest      weekly "still true?" list: important facts nobody confirmed for 90 days
"""
from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from typing import Any

from .local_hygiene import lexical_conflict
from .local_import import _tokens
from .local_model import Model

_FROM = re.compile(r"\b(?:switched|moved|migrated|changed|cambi[eéó]|pas[eéó]|migr[eéó])\b.*?\b(?:from|de)\s+([\wÁ-ú.-]{2,40})", re.I)
_NOW = re.compile(r"\b(?:now|ahora|ya no|no longer|anymore|instead|en vez de)\b", re.I)


def _d(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def strength(f: dict, today: dt.date | None = None) -> float:
    today = today or dt.date.today()
    if f.get("importance", 0) >= 1.0:
        return 1.0
    last = _d(f.get("last_used_at")) or _d(f.get("confirmed_at")) or _d(f.get("recorded_at")) or today
    s_days = 60.0 * (1 + int(f.get("use_count") or 0))
    return float(f.get("importance") or 0.5) * math.exp(-max(0, (today - last).days) / s_days)


def replaces(newer: dict, older: dict) -> bool:
    """Does the newer fact make the older one no longer current? Cheap, deterministic rules."""
    a, b = newer["text"], older["text"]
    m = _FROM.search(a)
    if m and m.group(1).lower() in b.lower():
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    if overlap >= 0.5 and lexical_conflict(a, b):
        return True
    return bool(overlap >= 0.6 and _NOW.search(a) and newer.get("kind") == older.get("kind"))


async def _summary(llm: Any, name: str, facts: list[dict]) -> tuple[str, list[str]]:
    top = sorted(facts, key=lambda f: -float(f.get("importance") or 0))[:12]
    if llm is not None:
        lines = "\n".join(f"[{i + 1}] {f['text']}" for i, f in enumerate(top))
        prompt = (f"Facts about {name}:\n{lines}\n\nWrite 2-4 short sentences summarising what matters about {name}, "
                  "using ONLY these facts. After each sentence list the numbers of the facts it uses. Reply JSON only: "
                  '{"sentences": [{"text": "...", "facts": [1, 2]}]}. Same language as the facts.')
        try:
            r = await llm.json(prompt)
            out, used = [], []
            for s in (r.get("sentences") or [])[:4]:
                ids = [int(x) for x in (s.get("facts") or []) if str(x).isdigit() and 1 <= int(x) <= len(top)]
                if ids and isinstance(s.get("text"), str) and s["text"].strip():
                    out.append(s["text"].strip() + " " + "".join(f"[{i}]" for i in ids))
                    used += [top[i - 1]["note_id"] for i in ids]
            if out:
                return " ".join(out), list(dict.fromkeys(used))
        except Exception:  # noqa: BLE001 — fall back to the facts themselves
            pass
    pick = top[:3]
    return " ".join(f"{f['text'].rstrip('.')}. [{i + 1}]" for i, f in enumerate(pick)), [f["note_id"] for f in pick]


SECTIONS = ("About", "Preferences", "People", "Working on", "Goals")


def build_profile(m: Model, today: dt.date | None = None, limit: int = 2400) -> tuple[str, list[str]]:
    facts = [f for f in m.facts() if f["status"] == "active"]
    ents = {e["id"]: e for e in m.entities()}
    rank = sorted(facts, key=lambda f: -strength(f, today))
    sec: dict[str, list[str]] = {s: [] for s in SECTIONS}
    used: list[str] = []
    size = 0

    def put(s: str, text: str, fid: str | None) -> None:
        nonlocal size
        t = " ".join(text.split())[:300]
        if size + len(t) > limit or t in sec[s]:
            return
        sec[s].append(t)
        size += len(t) + 3
        if fid:
            used.append(fid)
    for f in rank:
        ek = f.get("entity_kind")
        if f["kind"] == "goal":
            put("Goals", f["text"], f["note_id"])
        elif ek == "me" and f["kind"] in ("preference",):
            put("Preferences", f["text"], f["note_id"])
        elif ek == "me" or f["kind"] in ("identity",):
            put("About", f["text"], f["note_id"])
        elif ek == "person" or f["kind"] == "relationship":
            put("People", f["text"], f["note_id"])
    for e in sorted(ents.values(), key=lambda e: -float(e["importance"] or 0)):
        if e["kind"] in ("project", "org", "tool") and e.get("n"):
            put("Working on", f"{e['name']}: {(e.get('summary') or '').split('[')[0].strip() or 'see its page'}", None)
    text = "\n\n".join(f"## {s}\n" + "\n".join(f"- {x}" for x in v) for s, v in sec.items() if v)
    return text, used


async def consolidate(m: Model, llm: Any = None, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    job = f"cons-{today.isoformat()}-{uuid.uuid4().hex[:6]}"
    st = {"job": job, "expired": 0, "superseded": 0, "summaries": 0}
    for f in m.facts():
        exp = _d(f.get("expires_at"))
        if exp and exp < today:
            m.set_fact(f["note_id"], job, status="expired", valid_to=exp.isoformat())
            st["expired"] += 1
    for e in m.entities():
        if not e["dirty"]:
            continue
        fs = sorted(m.facts(e["id"]), key=lambda f: str(f["recorded_at"]))
        for i, newer in enumerate(fs):
            for older in fs[:i]:
                if older["status"] == "active" and older["note_id"] != newer["note_id"] and replaces(newer, older):
                    m.set_fact(older["note_id"], job, status="superseded", superseded_by=newer["note_id"],
                               valid_to=str(newer["recorded_at"])[:10])
                    older["status"] = "superseded"
                    st["superseded"] += 1
        live = [f for f in fs if f["status"] == "active"]
        with m.db() as c:
            if len(live) >= 2:
                summ, ids = await _summary(llm, e["name"], live)
                import json as _j
                c.execute("UPDATE entities SET summary=?, summary_facts=?, summary_at=?, dirty=0, importance=? WHERE id=?",
                          (summ, _j.dumps(ids), today.isoformat(), min(1.0, 0.3 + 0.1 * len(live)), e["id"]))
                st["summaries"] += 1
            else:
                c.execute("UPDATE entities SET dirty=0, importance=? WHERE id=?", (min(1.0, 0.3 + 0.1 * len(live)), e["id"]))
    text, ids = build_profile(m, today)
    import json as _j
    with m.db() as c:
        c.execute("INSERT OR REPLACE INTO profile(id, text, sections, fact_ids, built_at) VALUES (1,?,?,?,?)",
                  (text, "[]", _j.dumps(ids), today.isoformat()))
    st["profile_chars"] = len(text)
    return st


def digest(m: Model, today: dt.date | None = None, n: int = 8) -> list[dict]:
    today = today or dt.date.today()
    out = []
    for f in sorted(m.facts(), key=lambda f: str(f["recorded_at"])):
        c = _d(f.get("confirmed_at"))
        if float(f.get("importance") or 0) >= 0.6 and (not c or (today - c).days > 90):
            out.append({k: f[k] for k in ("note_id", "text", "entity", "kind", "recorded_at", "source_app")})
        if len(out) >= n:
            break
    return out


def profile_text(m: Model) -> str:
    with m.db() as c:
        r = c.execute("SELECT text FROM profile WHERE id=1").fetchone()
    return (r["text"] if r else "") or ""
