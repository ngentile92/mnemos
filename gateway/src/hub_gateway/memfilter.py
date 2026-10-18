"""Write filter for memory candidates (local mode): cheap, deterministic rules that run before and after any model.

Research (eval/README.md, persona eval): ~70% of what a small model extracts from chats is project trivia (paths,
endpoints, errors, commands), assistant narration or generic knowledge. Those belong in the searchable chat index
(local_evidence), never in memory notes. Sensitive topics (health, religion, politics, sexuality, ID numbers) are not
stored by default (same default as Claude's memory); time-bound facts get an expiry instead of becoming permanent.

  classify(text)      -> {"keep", "reason", "durability", "expires_at", "sensitive"}
  keep_texts(texts)   -> the texts worth keeping as durable notes
  clean_message(msg)  -> a user message without code blocks, pasted logs/emails and quoted text (before a model sees it)
  extract_conversation(llm, msgs, ...) -> typed facts from one conversation (labels, filtered)
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

# --- trivia: things that are derivable from code, logs or the project, not facts about the person
_TRIVIA = [
    ("url", re.compile(r"https?://|www\.\w|\b\w+\.(?:com|io|dev|org|net|app)/\S", re.I)),
    ("localhost/port", re.compile(r"\blocalhost\b|127\.0\.0\.1|0\.0\.0\.0|\bport\s+\d{2,5}\b|:\d{4,5}\b", re.I)),
    ("path", re.compile(r"(?:^|[\s(\"'`])(?:~|\.{1,2})?/[\w.+@{}-]+/[\w.+@{}/-]*|[A-Za-z]:\\\\|\b[\w-]+/[\w.-]+/[\w.-]+")),
    ("file", re.compile(r"\b[\w+-]+\.(?:py|js|mjs|ts|tsx|jsx|json|ya?ml|toml|sql|sh|rs|go|java|kt|rb|php|css|html|svelte|vue|"
                        r"lock|env|cfg|ini|md|txt|csv|log)\b", re.I)),
    ("stack trace", re.compile(r"Traceback \(most recent|\bat [\w.$]+\(|\w+(?:Error|Exception):|\berror\s*(?:code)?\s*[:#]\s*\w|"
                               r"\bline \d+\b|\bexit code\b|\bsegfault\b", re.I)),
    ("code", re.compile(r"`|=>|::|\(\)|\{\w*\}|\w+\(\w*\)|\bnpm run\b|\bpip install\b|\bgit (?:push|pull|rebase|merge|commit)\b|"
                        r"\bPromise<|\b(?:const|let|var|def|import|return)\s+\w|\b[a-z]+[A-Z]\w*\b|\b\w+_\w+\b")),
    ("hash/id", re.compile(r"\b[0-9a-f]{7,40}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-", re.I)),
    ("version pin", re.compile(r"\bv?\d+\.\d+\.\d+\b")),
    ("branch/commit", re.compile(r"\b(?:branch|the commit|PR|pull request|migration|staging|rebase)\b", re.I)),
    ("size/timing", re.compile(r"\b\d+(?:\.\d+)?\s?(?:GB|MB|KB|ms|seconds?|segundos)\b", re.I)),
    ("table/schema", re.compile(r"\b[\w-]+\.[\w-]+\.[\w-]+\b|\b(?:column|table|schema|endpoint|variable|dependency)\b", re.I)),
]
# assistant narration, one-off requests, acknowledgements, generic knowledge
_NOISE = [
    ("assistant", re.compile(r"\b(?:the )?(?:assistant|AI|model|Claude|ChatGPT|Grok)\s+(?:suggested|said|proposed|recommended|"
                             r"is|will|should)\b|\bthe user is (?:asking|trying|wondering)\b|\bel usuario (?:pregunta|quiere)\b",
                             re.I)),
    ("request", re.compile(r"\b(?:asked|wants?|needs?) (?:the assistant|you|it|to (?:fix|rename|add|remove|run|change|update|"
                           r"debug|refactor))\b|\b(?:pidió|quiere) que\b|\bfix (?:the|this)\b", re.I)),
    ("ack", re.compile(r"^\W*(?:she |he |they |user )?(?:said )?(?:(?:ok(?:ay)?|perfect|perfecto|dale|thanks?|thank you|gracias|"
                       r"genial|great|cool|go ahead|listo|sí|si|yes|no|that works|funciona|seguí)[\s,.!]*)+$", re.I)),
    ("pasted", re.compile(r"\b(?:pasted|pegó|copied|here is my|acá está)\b", re.I)),
    ("generic", re.compile(r"^\s*(?:[A-Z][\w.+#-]*\s){1,3}(?:is|are|es|son)\s+(?:a|an|un|una)\s+(?:\w+\s){0,4}"
                           r"(?:language|framework|library|tool|database|lenguaje|herramienta|librería|base de datos)\b", re.I)),
]
_SENSITIVE = [
    ("health", re.compile(r"\b(?:sertralin\w*|fluoxetin\w*|antidepress\w*|anxiety|ansiedad|depression|depresi[oó]n|diagnos\w+|"
                          r"therapy|terapia|psychiatr\w+|psiquiatr\w+|medication|medicaci[oó]n|cancer|c[aá]ncer|HIV|VIH|"
                          r"diabetes|pregnan\w+|embaraz\w+|disorder|trastorno|ADHD|TDAH|bipolar)\b", re.I)),
    ("religion", re.compile(r"\b(?:catholic|cat[oó]lic\w+|christian|cristian\w+|jewish|jud[ií]\w+|muslim|musulm\w+|atheist|ateo|"
                            r"religio\w+|church|iglesia|mosque|synagogue)\b", re.I)),
    ("politics", re.compile(r"\b(?:votes? for|vota (?:a|por)|vot[oó]|partido|political party|peronis\w+|kirchner\w+|libertari\w+|"
                            r"republican|democrat|conservative party|labour party)\b", re.I)),
    ("sexuality", re.compile(r"\b(?:gay|lesbian|lesbiana|bisexual|transgender|sexual orientation|orientaci[oó]n sexual)\b", re.I)),
    ("id number", re.compile(r"\b(?:DNI|CUIT|CUIL|SSN|passport|pasaporte|social security|PPS)\b.{0,20}\d|\b\d{2}\.\d{3}\.\d{3}\b",
                             re.I)),
]
_TRANSIENT = [
    (1, re.compile(r"\b(?:today|tonight|hoy|esta noche)\b", re.I)),
    (2, re.compile(r"\b(?:tomorrow|mañana)\b", re.I)),
    (14, re.compile(r"\b(?:next week|this week|la semana que viene|la pr[oó]xima semana|esta semana|this weekend|"
                    r"este finde|in a few days|en unos d[ií]as)\b", re.I)),
    (45, re.compile(r"\b(?:next month|this month|el mes que viene|el pr[oó]ximo mes|este mes)\b", re.I)),
]


def trivia_reason(text: str) -> str | None:
    for name, rx in _TRIVIA + _NOISE:
        if rx.search(text):
            return name
    return None


def sensitive_reason(text: str) -> str | None:
    for name, rx in _SENSITIVE:
        if rx.search(text):
            return name
    return None


def expiry_days(text: str) -> int | None:
    for days, rx in _TRANSIENT:
        if rx.search(text):
            return days
    return None


def classify(text: str, allow_sensitive: bool = False, now: dt.date | None = None) -> dict[str, Any]:
    from .local_discover import has_secret
    t = (text or "").strip()
    out: dict[str, Any] = {"text": t, "keep": False, "reason": None, "durability": "long", "expires_at": None,
                           "sensitive": None}
    if not 10 <= len(t) <= 300:
        out["reason"] = "length"
    elif has_secret(t):
        out["reason"] = "secret"
    elif (r := trivia_reason(t)):
        out["reason"] = r
    elif (s := sensitive_reason(t)) and not allow_sensitive:
        out["reason"], out["sensitive"] = "sensitive", s
    else:
        out["keep"] = True
        if (d := expiry_days(t)):
            out["durability"] = "short"
            out["expires_at"] = ((now or dt.date.today()) + dt.timedelta(days=d)).isoformat()
    return out


def keep_texts(texts: list[str], allow_sensitive: bool = False) -> list[str]:
    """Durable keepers only (time-bound facts are kept by callers that can store an expiry)."""
    out = []
    for t in texts:
        c = classify(t, allow_sensitive)
        if c["keep"] and c["durability"] != "short":
            out.append(c["text"])
    return out


# --- message cleaning before a model sees it
_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_QUOTE = re.compile(r"^\s*>.*$", re.M)
_LOGLINE = re.compile(r"^\s*(?:at |File \"|Traceback|\$ |\[\d|\d{4}-\d\d-\d\d[ T]\d\d:|[A-Z]+\s+\d{3}\b|npm |error |warning ).*$",
                      re.M | re.I)
_ACK = re.compile(r"^\W*(?:ok(?:ay)?|perfect\w*|dale|thanks?|gracias|genial|listo|s[ií]|yes|go ahead|seguí|segui)\W*$", re.I)


def clean_message(msg: str, max_len: int = 1500) -> str:
    """Drop code blocks, quoted text, log lines and acks; very long messages are pastes (documents) → ''."""
    if not msg or len(msg) > 8000:
        return ""
    m = _QUOTE.sub("", _FENCE.sub(" ", msg))
    m = _LOGLINE.sub("", m)
    m = re.sub(r"\n{3,}", "\n\n", m).strip()
    if len(m) < 12 or _ACK.match(m) or m.lstrip().startswith("/"):
        return ""
    return m[:max_len]


# --- typed extraction (one conversation at a time, with a running summary)
KINDS = ("identity", "preference", "relationship", "project_state", "decision", "goal", "skill_fact", "event", "transient")
DURABILITY = ("permanent", "long", "medium", "short")
PROMPT = (
    "You read messages one person wrote to AI assistants in ONE conversation{when}{app}. {summary}"
    "Extract facts ABOUT THIS PERSON that would still matter in 6 months: who they are, role, where they live, "
    "people in their life or work, their projects (goal, stack, decisions WITH the reason), lasting preferences, "
    "routines, goals. Write each fact as one sentence that makes sense alone, naming the project or person (never "
    "'this', 'it'). Do NOT extract: file names, paths, URLs, ports, endpoints, errors, commands, code, variable or "
    "table names, one-off requests to the assistant, anything the assistant said, general knowledge, secrets, health, "
    "religion, politics. Most conversations have 0–3 facts; return [] if none.\n"
    'Reply JSON only: {{"topic": "<what this conversation is about, 8 words>", "facts": [{{"text": "...", '
    '"kind": "identity|preference|relationship|project_state|decision|goal|skill_fact|event|transient", '
    '"about": "<me or the person/project/tool it is about>", "importance": 1-5, '
    '"durability": "permanent|long|medium|short", "quote": "<the person\'s words, max 15 words>"}}]}}. '
    "Write facts in the language the person writes.\n\nMESSAGES:\n")


def _norm_fact(f: Any) -> dict[str, Any] | None:
    if isinstance(f, str):
        f = {"text": f}
    if not isinstance(f, dict) or not isinstance(f.get("text"), str):
        return None
    kind = str(f.get("kind") or "").strip().lower()
    dur = str(f.get("durability") or "").strip().lower()
    try:
        imp = max(1, min(5, int(f.get("importance") or 3)))
    except (TypeError, ValueError):
        imp = 3
    return {"text": f["text"].strip(), "kind": kind if kind in KINDS else None,
            "durability": dur if dur in DURABILITY else "long", "importance": imp,
            "about": str(f.get("about") or "me").strip()[:80], "quote": str(f.get("quote") or "").strip()[:200]}


def gate(facts: list[dict], allow_sensitive: bool = False, now: dt.date | None = None) -> list[dict]:
    """Deterministic filter + label policy: drop trivia/sensitive/low-importance; short-lived facts get an expiry."""
    out = []
    for f in facts:
        c = classify(f["text"], allow_sensitive, now)
        if not c["keep"]:
            continue
        short = c["durability"] == "short" or f["durability"] == "short" or f["kind"] == "transient"
        if f["kind"] == "event" and not short and f["importance"] < 4:
            continue  # one-off events are evidence, not memory
        if f["importance"] <= 1 or (f["importance"] == 2 and f["durability"] not in ("permanent", "long")):
            continue
        g = {**f, "sensitive": c["sensitive"]}
        if short:
            g["durability"] = "short"
            g["expires_at"] = c["expires_at"] or ((now or dt.date.today()) + dt.timedelta(days=14)).isoformat()
        out.append(g)
    return out


def windows(msgs: list[str], size: int = 6000) -> list[str]:
    out, cur = [], ""
    for m in msgs:
        m = clean_message(m)
        if not m:
            continue
        from .local_discover import has_secret
        if has_secret(m):
            continue
        if cur and len(cur) + len(m) > size:
            out.append(cur)
            cur = ""
        cur += "- " + m.replace("\n", " ") + "\n"
    if cur:
        out.append(cur)
    return out


async def extract_conversation(llm: Any, msgs: list[str], app: str = "", ts: str = "", allow_sensitive: bool = False,
                               summary: str = "") -> list[dict]:
    """Typed, gated facts from one conversation. Windows of a long chat share a running topic summary."""
    out: list[dict] = []
    topic = summary
    for w in windows(msgs):
        prompt = PROMPT.format(when=f" on {ts[:10]}" if ts else "", app=f" in {app}" if app else "",
                               summary=f"So far it was about: {topic}. " if topic else "")
        try:
            r = await llm.json(prompt + w)
        except Exception:  # noqa: BLE001 — one bad call does not stop the import
            continue
        if not isinstance(r, dict):
            continue
        topic = str(r.get("topic") or topic)[:120]
        raw = r.get("facts") or []
        out += gate([f for f in (_norm_fact(x) for x in raw if x) if f], allow_sensitive)
    seen, uniq = set(), []
    for f in out:
        k = f["text"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append({**f, "topic": topic})
    return uniq
