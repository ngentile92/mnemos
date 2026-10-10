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
- "action": "update" (rewrite only that part) or "obsolete" (the whole note is no longer true)
- "proposed_text": for update, the FULL note with only the evidence part changed; keep every other word.
  Do not paste the user's sentence at the start or end.
- "why": one short sentence in the note's language

Reply JSON: {{"matches": [ ... ]}}  (empty list if no note states it)"""

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


async def propose(url: str, model: str, correction: str, notes: list[dict[str, Any]], *, timeout: float = 60,
                  transport: httpx.AsyncBaseTransport | None = None, max_out: int = 2) -> list[dict[str, Any]]:
    if not notes:
        return []
    listing = "\n\n".join(f"[{i}] {_norm(str(n.get('text', '')))[:1500]}" for i, n in enumerate(notes, 1))
    payload = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": "json",
               "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700},
               "messages": [{"role": "user", "content": PROMPT.format(correction=correction, notes=listing)}]}
    async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False) as c:
        r = await c.post(f"{url.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
    try:
        data = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    except json.JSONDecodeError:
        data = {}
    out, seen = [], set()
    for m in data.get("matches") or []:
        try:
            n = int(m.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(notes) or n in seen:
            continue
        note = notes[n - 1]
        ok, _ = check(correction, str(note.get("text", "")), m)
        if not ok or not relevant(correction, str(m.get("evidence", ""))):
            continue
        seen.add(n)
        p = {"id": note.get("id"), "dataset": note.get("dataset"), "text": note.get("text", ""), "rank": n,
             "action": m["action"], "evidence": _norm(str(m["evidence"])), "why": str(m.get("why") or "")[:300]}
        if m["action"] == "update":
            p["proposed_text"] = _clean(str(m["proposed_text"]))
            p["diff"] = word_diff(str(note.get("text", "")), p["proposed_text"])
        out.append(p)
    out.sort(key=lambda p: p["rank"])  # retrieval order: most relevant first
    return out[:max_out]
