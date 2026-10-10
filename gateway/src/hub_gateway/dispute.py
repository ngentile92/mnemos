"""'This isn't right' (memory_dispute): find the notes that STATE what the user corrects and propose minimal fixes.

One local-LLM call for all candidates, then strict checks so a weak model cannot do damage:
  * the model must quote the evidence (the exact words in the note that the correction contradicts) and the
    quote must really be in the note;
  * an update must change the note AT the evidence (not elsewhere) and must not just prepend/append the user's
    sentence to an unrelated note;
  * notes about other topics are dropped. Proposals come with a word diff. Nothing is written here.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

import httpx

KEEP_ALIVE = __import__("os").environ.get("HUB_OLLAMA_KEEP_ALIVE", "30m")  # keep local models warm: first call loads them (~10 s)

PROMPT = """You maintain a personal memory. The user says this is the truth now:
CORRECTION: {correction}

Notes (numbered):
{notes}

Return ONLY the notes that explicitly state something this correction contradicts or makes outdated, about the
SAME subject. A note about another subject (home, hobbies, family, tools...) must NOT be returned, even if it
shares words. Most of the time zero or one note matches.

For each match give:
- "n": the note number
- "evidence": the exact words copied from that note that the correction contradicts (verbatim, 3-25 words)
- "action": "update" (only that part is wrong) or "obsolete" (the whole note is no longer true)
- "why": one short sentence in the note's language

Reply JSON: {{"matches": [ ... ]}}  (empty list if no note states it). Be brief."""

REWRITE = """Rewrite this note so it agrees with the correction. Change ONLY the quoted part; copy every other word
exactly. Do not add the correction sentence at the start or the end.
CORRECTION: {correction}
PART TO CHANGE: "{evidence}"
NOTE:
{text}

Reply JSON: {{"proposed_text": "the full corrected note"}}"""

WS = re.compile(r"\s+")
WORD = re.compile(r"\w+", re.UNICODE)
STOP = set("""a al algo como con de del el en es esta este esto hoy la las lo los mas me mi muy no o para pero por que
se ser si sin su sus un una y ya the of to in is it and or not for on with at by from this that are was be have has i
my now""".split())


def _norm(s: str) -> str:
    return WS.sub(" ", s or "").strip()


def _clean(s: str) -> str:
    return re.sub(r"^\[\d+\]\s*", "", _norm(s))  # the model sometimes copies the "[n]" label


def _content(s: str) -> set[str]:
    return {w[:5] for w in WORD.findall(s.lower()) if len(w) > 2 and w not in STOP}


def word_diff(old: str, new: str) -> list[dict[str, str]]:
    a, b = _norm(old).split(" "), _norm(new).split(" ")
    out: list[dict[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "same", "text": " ".join(a[i1:i2])})
        else:
            if i2 > i1:
                out.append({"op": "del", "text": " ".join(a[i1:i2])})
            if j2 > j1:
                out.append({"op": "add", "text": " ".join(b[j1:j2])})
    return out


def check(correction: str, note: str, m: dict[str, Any]) -> tuple[bool, str]:
    """Reject proposals that are not grounded or that are not minimal edits at the evidence."""
    text, ev = _norm(note), _norm(str(m.get("evidence") or ""))
    if len(ev) < 8 or ev.lower() not in text.lower():
        return False, "evidence not found in the note"
    action = m.get("action")
    if action not in ("update", "obsolete"):
        return False, "bad action"
    if action == "obsolete":
        return True, ""
    new = _clean(str(m.get("proposed_text") or ""))
    if len(new) < 3 or new == text:
        return False, "no change"
    corr = _norm(correction).lower().rstrip(".;: ")
    if corr and (new.lower().startswith(corr) or new.lower().endswith(corr)) and text.lower() in new.lower():
        return False, "just prepends/appends the correction"
    if text.lower() in new.lower() and not new.lower().startswith(text.lower()):
        return False, "text glued in front of the note"
    a = text.lower()
    start = a.find(ev.lower())
    end = start + len(ev)
    sm = difflib.SequenceMatcher(a=a, b=new.lower(), autojunk=False)
    touched = [(i1, i2) for op, i1, i2, _, _ in sm.get_opcodes() if op != "equal"]
    if not any(i1 <= end + 1 and i2 >= start - 1 for i1, i2 in touched):
        return False, "change is not at the evidence"
    outside = sum(i2 - i1 for i1, i2 in touched if i2 < start - 1 or i1 > end + 1)
    if outside > max(20, len(ev)):
        return False, "rewrites parts unrelated to the evidence"
    return True, ""


def relevant(correction: str, evidence: str, why: str = "") -> bool:
    """Cheap topical guard: the evidence must share at least one content word stem with the correction."""
    return bool(_content(correction) & _content(evidence))


_CACHE: dict[str, tuple[list, list]] = {}


def _key(model: str, correction: str, notes: list[dict[str, Any]]) -> str:
    import hashlib
    h = hashlib.sha256(f"{model}\x00{_norm(correction).lower()}".encode())
    for n in notes:
        h.update(f"\x00{n.get('id')}\x00{n.get('text', '')}".encode())
    return h.hexdigest()


async def propose_explained(url: str, model: str, correction: str, notes: list[dict[str, Any]], **kw: Any
                            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """(proposals, checked, cached). `checked` says, for every candidate note, why it was or was not proposed.
    Same input (correction + same candidate notes and texts) → same answer, served from an in-process cache."""
    k = _key(model, correction, notes)
    if k in _CACHE:
        props, checked = _CACHE[k]
        return props, checked, True
    checked: list[dict[str, Any]] = []
    props = await propose(url, model, correction, notes, checked=checked, **kw)
    if len(_CACHE) > 256:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[k] = (props, checked)
    return props, checked, False


async def propose(url: str, model: str, correction: str, notes: list[dict[str, Any]], *, timeout: float = 60,
                  transport: httpx.AsyncBaseTransport | None = None, max_out: int = 2,
                  checked: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    verdict: dict[int, str] = {}
    if not notes:
        return []
    listing = "\n\n".join(f"[{i}] {_norm(str(n.get('text', '')))[:1200]}" for i, n in enumerate(notes, 1))

    async def ask(prompt: str, limit: int) -> dict:
        payload = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": "json",
                   "options": {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": limit},
                   "messages": [{"role": "user", "content": prompt}]}
        async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False) as c:
            r = await c.post(f"{url.rstrip('/')}/api/chat", json=payload)
            r.raise_for_status()
        try:
            return json.loads(r.json().get("message", {}).get("content", "") or "{}")
        except json.JSONDecodeError:
            return {}

    # stage 1: which note states it (short answer, fast); stage 2: rewrite only the chosen note
    data = await ask(PROMPT.format(correction=correction, notes=listing), 300)
    out, seen = [], set()
    for m in data.get("matches") or []:
        try:
            n = int(m.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(notes) or n in seen:
            continue
        note = notes[n - 1]
        if len(out) >= max_out:
            break
        ev = _norm(str(m.get("evidence") or ""))
        if len(ev) < 8 or ev.lower() not in _norm(str(note.get("text", ""))).lower():
            verdict[n] = "model suggested it, rejected: evidence not found in the note"
            continue
        if not relevant(correction, ev):
            verdict[n] = "model suggested it, rejected: different subject from the correction"
            continue
        if m.get("action") == "update" and not m.get("proposed_text"):
            rw = await ask(REWRITE.format(correction=correction, evidence=ev, text=_norm(str(note.get("text", "")))), 600)
            m = {**m, "proposed_text": rw.get("proposed_text") or ""}
        ok, why = check(correction, str(note.get("text", "")), m)
        if ok and not relevant(correction, str(m.get("evidence", ""))):
            ok, why = False, "different subject from the correction"
        if not ok:
            verdict[n] = f"model suggested it, rejected: {why}"
            continue
        verdict[n] = "proposed"
        seen.add(n)
        p = {"id": note.get("id"), "dataset": note.get("dataset"), "text": note.get("text", ""), "rank": n,
             "action": m["action"], "evidence": _norm(str(m["evidence"])), "why": str(m.get("why") or "")[:300]}
        if m["action"] == "update":
            p["proposed_text"] = _clean(str(m["proposed_text"]))
            p["diff"] = word_diff(str(note.get("text", "")), p["proposed_text"])
        out.append(p)
    out.sort(key=lambda p: p["rank"])  # retrieval order: most relevant first
    out = out[:max_out]
    if checked is not None:
        kept = {p["rank"] for p in out}
        for i, n in enumerate(notes, 1):
            v = verdict.get(i, "does not state what the correction contradicts")
            if v == "proposed" and i not in kept:
                v = "relevant, but a better match was shown"
            checked.append({"id": n.get("id"), "dataset": n.get("dataset"), "preview": _norm(str(n.get("text", "")))[:140],
                            "verdict": v})
    return out
