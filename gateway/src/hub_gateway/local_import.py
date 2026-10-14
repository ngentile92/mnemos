"""Import existing memory into local mode: ChatGPT export, pasted Claude/ChatGPT memory, a folder of Markdown notes.

Two steps so nothing is written blindly: `preview` turns the input into candidate notes and flags the ones that
already exist (exact or near-duplicate, against the context's notes and within the batch); `commit` saves the
ones the user kept through memory_save (ledger, history and undo included), tagged `import-<source>`.
No LLM: ChatGPT conversations contribute only the user's own first-person statements ("I live in…", "my
daughter…", "prefiero…"), which is where durable facts about a person usually are.
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from typing import Any, Iterable

MAX_CANDIDATES = 2000
MAX_NOTE = 2000
SOURCES = ("chatgpt", "claude", "text", "markdown")

# first-person statements worth remembering (English + Spanish)
_FACT = re.compile(
    r"\b(i am|i'm|i was born|i live|i work|i studied|i have|i've got|i like|i love|i hate|i prefer|i don't like|"
    r"i usually|i always|i never|my (?:name|wife|husband|partner|son|daughter|kids?|children|mother|father|mom|dad|"
    r"brother|sister|dog|cat|job|company|boss|team|birthday|favou?rite|goal|plan|house|home|city|car)|"
    r"call me|"
    r"soy|me llamo|nací|vivo|trabajo|estudié|tengo|me gusta|me encanta|odio|prefiero|no me gusta|suelo|siempre|nunca|"
    r"mi (?:nombre|esposa|esposo|pareja|hijo|hija|hijos|madre|padre|mamá|papá|hermano|hermana|perro|gato|trabajo|"
    r"empresa|jefe|equipo|cumpleaños|favorit[oa]|objetivo|plan|casa|ciudad|auto|coche))\b", re.I)
_QUESTION = re.compile(r"\?\s*$")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _tokens(text: str) -> set[str]:
    return {w for w in norm(text).split() if len(w) > 2}


def similar(a: str, b: str, threshold: float = 0.8) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return norm(a) == norm(b)
    return len(ta & tb) / len(ta | tb) >= threshold


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -•*\t") for p in parts if p.strip(" -•*\t")]


# ------------------------------------------------------------------ parsers
def from_lines(text: str) -> list[str]:
    """Pasted memory (Claude 'what do you know about me', ChatGPT Settings → Memory): one fact per line/bullet."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s*", "", line).strip()
        if len(line) >= 8 and not line.endswith(":"):
            out.append(line[:MAX_NOTE])
    if len(out) <= 1 and len(text) > 200:  # one paragraph: split into sentences
        out = [s[:MAX_NOTE] for s in _sentences(text) if len(s) >= 8]
    return out


def _messages(conv: dict) -> Iterable[tuple[str, str]]:
    for node in (conv.get("mapping") or {}).values():
        msg = (node or {}).get("message") or {}
        role = (msg.get("author") or {}).get("role")
        parts = (msg.get("content") or {}).get("parts") or []
        text = " ".join(p for p in parts if isinstance(p, str)).strip()
        if role and text:
            yield role, text


def from_chatgpt(conversations: list[dict]) -> list[str]:
    """User's first-person statements from ChatGPT conversations.json (newest conversations first)."""
    convs = sorted((c for c in conversations if isinstance(c, dict)), key=lambda c: c.get("update_time") or 0, reverse=True)
    out = []
    for conv in convs:
        title = str(conv.get("title") or "").strip()
        for role, text in _messages(conv):
            if role != "user" or len(text) > 4000:  # long pastes are documents, not facts about the user
                continue
            for s in _sentences(text):
                if 15 <= len(s) <= 300 and _FACT.search(s) and not _QUESTION.search(s):
                    out.append(s if not title else f"{s} (from ChatGPT: {title[:60]})")
    return out


def chatgpt_bytes(data: bytes) -> list[str]:
    """conversations.json, or the export .zip that contains it."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            if not name:
                raise ValueError("this zip has no conversations.json (is it the ChatGPT export?)")
            data = z.read(name)
    try:
        convs = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("not a ChatGPT conversations.json") from e
    if not isinstance(convs, list):
        raise ValueError("not a ChatGPT conversations.json")
    return from_chatgpt(convs)


def from_markdown(files: list[dict]) -> list[str]:
    """Each file → one note, or one per section when it is long (split at headings)."""
    out = []
    for f in files:
        name = str(f.get("name", ""))
        if not name.lower().endswith((".md", ".markdown", ".txt")):
            continue
        text = str(f.get("text", "")).replace("\r\n", "\n")
        text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)  # front matter
        title = re.sub(r"\.(md|markdown|txt)$", "", name.rsplit("/", 1)[-1], flags=re.I)
        if len(text) <= MAX_NOTE:
            body = text.strip()
            if len(body) >= 8:
                out.append(body if body.lower().startswith(("#", title.lower())) else f"{title}: {body}")
            continue
        for sec in re.split(r"\n(?=#{1,3} )", text):
            sec = sec.strip()
            while len(sec) > MAX_NOTE:  # very long section: paragraphs
                cut = sec.rfind("\n\n", 0, MAX_NOTE)
                cut = cut if cut > 200 else MAX_NOTE
                out.append(f"{title}: {sec[:cut].strip()}")
                sec = sec[cut:].strip()
            if len(sec) >= 8:
                out.append(f"{title}: {sec}")
    return out


# ------------------------------------------------------------------ dedupe
def _near(tok: set[str], pool: list[set[str]], threshold: float = 0.8) -> bool:
    return bool(tok) and any(p and len(tok & p) / len(tok | p) >= threshold for p in pool)


def preview(candidates: list[str], existing: list[str]) -> dict[str, Any]:
    ex_norm = {norm(t) for t in existing}
    ex_tok = [_tokens(t) for t in existing]
    kept: list[str] = []
    kept_norm: set[str] = set()
    kept_tok: list[set[str]] = []
    items = []
    for text in candidates[: MAX_CANDIDATES * 3]:
        text = text.strip()[:MAX_NOTE]
        if len(text) < 8:
            continue
        n, tok = norm(text), _tokens(text)
        dup = None
        if n in ex_norm:
            dup = "already in your memory"
        elif _near(tok, ex_tok):
            dup = "very similar to a note you have"
        elif n in kept_norm or _near(tok, kept_tok):
            dup = "repeated in this import"
        items.append({"text": text, "duplicate": dup})
        if not dup:
            kept.append(text)
            kept_norm.add(n)
            kept_tok.append(tok)
        if len(items) >= MAX_CANDIDATES:
            break
    return {"items": items, "new": len(kept), "duplicates": len(items) - len(kept),
            "truncated": len(candidates) > len(items)}


def candidates(source: str, body: dict) -> list[str]:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    if source in ("claude", "text"):
        return from_lines(str(body.get("text", ""))[:2_000_000])
    if source == "markdown":
        files = body.get("files") or []
        if not isinstance(files, list):
            raise ValueError("files must be a list")
        return from_markdown(files[:5000])
    text = str(body.get("text", ""))
    if text.strip().startswith("["):
        return chatgpt_bytes(text.encode())
    return from_lines(text)  # pasted ChatGPT memory list
