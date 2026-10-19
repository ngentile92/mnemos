"""Multi-signal ranking for local search (v0.9): the hybrid keyword + meaning order, re-weighted by what the memory
model knows about each note: importance, strength (use and recency), whether it is still current, and whether the
query names the entity it is about. Each result gets a short "why" (who/what it is about, where it came from, when),
and use is counted so unused facts fade from the profile instead of being deleted.
"""
from __future__ import annotations

import re
from typing import Any

from .local_consolidate import strength
from .local_model import Model

TEMPORAL = re.compile(r"\b(?:before|used to|previous(?:ly)?|history|antes|sol[ií]a|anterior|historial|when did|cu[aá]ndo)\b",
                      re.I)


def mentioned(m: Model, query: str) -> list[dict]:
    low = query.lower()
    out = []
    for e in m.entities():
        if e["kind"] == "me":
            continue
        for nm in [e["name"], *e["aliases"]]:
            if len(nm) >= 3 and re.search(r"\b" + re.escape(nm.lower()) + r"\b", low):
                out.append(e)
                break
    return out


def rerank(m: Model, query: str, results: list[dict[str, Any]], touch: bool = True) -> list[dict[str, Any]]:
    ents = {e["id"] for e in mentioned(m, query)}
    temporal = bool(TEMPORAL.search(query))
    scored = []
    for rank, r in enumerate(results, 1):
        base = 1.0 / (20 + rank)
        f = m.fact(str(r.get("id") or ""))
        if not f:
            scored.append((base, r))
            continue
        imp = float(f.get("importance") or 0.5)
        s = base * (0.5 + imp) * (0.7 + 0.3 * min(1.0, strength(f) / max(imp, 0.05)))
        if f.get("entity_id") in ents:
            s *= 1.6
        if f["status"] != "active":
            if not temporal:
                s *= 0.3
            r = {**r, "status": f["status"], **({"valid_to": f["valid_to"]} if f.get("valid_to") else {})}
        why = {"about": f.get("entity"), "kind": f.get("kind"), "from": f.get("source_app"),
               "saved": str(f.get("recorded_at") or "")[:10]}
        if f.get("quote"):
            why["quote"] = f["quote"]
        scored.append((s, {**r, "why": {k: v for k, v in why.items() if v}}))
    out = [r for _s, r in sorted(scored, key=lambda x: -x[0])]
    if touch:
        m.touch([str(r.get("id")) for r in out[:5] if r.get("id")])
    return out


def entity_cards(m: Model, query: str, n: int = 2) -> list[dict[str, Any]]:
    return [{"entity": e["name"], "kind": e["kind"], "summary": e["summary"]} for e in mentioned(m, query)
            if e.get("summary")][:n]
