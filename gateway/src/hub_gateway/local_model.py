"""Layered memory model (local mode, v0.9). One SQLite file per space: data/<space>/model.sqlite.

  L0 profile   per space: short sections rebuilt nightly from the facts (local_consolidate)
  L1 entities  person / project / org / tool / place / topic / me, with a summary that cites fact ids
  L2 facts     one row per note (the note text stays in the store; this adds what the note is about, its kind,
               importance, durability, validity dates, confidence, origin, source app, the person's own words,
               usage, and status active / superseded / expired)
  L3 evidence  raw chat messages (local_evidence, unchanged)

Writes are ADD-only: saving never edits or deletes another note. A new fact is linked to its entity (aliases,
[[Name]] links, known names in the text, the extractor's "about"); whether it replaces an older one is decided later,
per entity, by the consolidation pass, which marks the old fact superseded (kept, with valid_to) instead of
deleting it. Migrating existing notes only adds rows here, so its undo is exact: notes are never touched.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

KINDS = ("identity", "preference", "relationship", "project_state", "decision", "goal", "skill_fact", "event", "transient")
ENTITY_KINDS = ("me", "person", "project", "org", "tool", "place", "topic")
SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, name TEXT, kind TEXT, aliases TEXT DEFAULT '[]',
  summary TEXT, summary_facts TEXT DEFAULT '[]', summary_at TEXT, importance REAL DEFAULT 0.5, pinned INT DEFAULT 0,
  status TEXT DEFAULT 'active', dirty INT DEFAULT 1, created_at TEXT, batch TEXT);
CREATE TABLE IF NOT EXISTS facts(note_id TEXT PRIMARY KEY, text TEXT, entity_id TEXT, kind TEXT,
  importance REAL DEFAULT 0.5, durability TEXT DEFAULT 'long', expires_at TEXT, valid_from TEXT, valid_to TEXT,
  superseded_by TEXT, confidence REAL DEFAULT 0.7, origin TEXT DEFAULT 'imported', source_app TEXT, quote TEXT,
  recorded_at TEXT, last_used_at TEXT, use_count INT DEFAULT 0, not_relevant INT DEFAULT 0, confirmed_at TEXT,
  status TEXT DEFAULT 'active', batch TEXT);
CREATE INDEX IF NOT EXISTS f_ent ON facts(entity_id, status);
CREATE TABLE IF NOT EXISTS links(a TEXT, b TEXT, kind TEXT, created_at TEXT, PRIMARY KEY(a, b, kind));
CREATE TABLE IF NOT EXISTS profile(id INT PRIMARY KEY CHECK(id=1), text TEXT, sections TEXT, fact_ids TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, ts TEXT, job TEXT, note_id TEXT, field TEXT, old TEXT, new TEXT);
"""
_WIKI = re.compile(r"\[\[([^\]\n]{2,60})\]\]")
_CAP = re.compile(r"\b([A-ZÁÉÍÓÚÑ][\wáéíóúñ'-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ'-]+){0,2})")
_STOP = {"I", "The", "A", "An", "El", "La", "Los", "Las", "Un", "Una", "Mi", "My", "She", "He", "They", "We", "It",
         "This", "That", "Prefers", "Prefiere", "Uses", "Usa", "Works", "Trabaja", "Lives", "Vive", "Wants", "Quiere",
         "Goal", "Her", "His", "Su", "Sus", "En", "In", "On", "For", "Para", "Since", "Desde", "Monday", "Friday"}
# Well-known products and technologies: typed "tool", and never the subject of a fact just for being named in it
# ("opened a PR on GitHub" is about the work, not about GitHub). Extend with MNEMOS_KNOWN_TOOLS="Foo,Bar Baz".
KNOWN_TOOLS = {
    "github", "gitlab", "bitbucket", "git", "jira", "linear", "clickup", "asana", "trello", "notion", "obsidian",
    "airtable", "confluence", "slack", "discord", "teams", "microsoft teams", "zoom", "google meet", "meet", "whatsapp",
    "telegram", "signal", "gmail", "outlook", "google calendar", "calendar", "google drive", "drive", "google docs",
    "google sheets", "dropbox", "figma", "canva", "excel", "word", "powerpoint", "keynote", "claude", "claude code",
    "claude desktop", "chatgpt", "openai", "anthropic", "gemini", "grok", "copilot", "github copilot", "cursor", "codex",
    "ollama", "lm studio", "vs code", "vscode", "visual studio code", "neovim", "vim", "emacs", "xcode", "terminal",
    "iterm", "docker", "docker desktop", "kubernetes", "k8s", "helm", "terraform", "ansible", "tailscale", "wireguard",
    "vpn", "wifi", "wi-fi", "bluetooth", "aws", "gcp", "google cloud", "azure", "vercel", "netlify", "cloudflare",
    "heroku", "fly.io", "render", "supabase", "firebase", "postgres", "postgresql", "mysql", "sqlite", "redis",
    "mongodb", "elasticsearch", "kafka", "rabbitmq", "python", "javascript", "typescript", "go", "golang", "rust",
    "java", "kotlin", "swift", "ruby", "php", "node", "node.js", "deno", "bun", "react", "next.js", "nextjs", "vue",
    "svelte", "sveltekit", "angular", "django", "fastapi", "flask", "rails", "tauri", "electron", "npm", "pip", "uv",
    "homebrew", "macos", "ios", "android", "windows", "linux", "ubuntu", "iphone", "ipad", "mac", "chrome", "safari",
    "firefox", "twitter", "x", "linkedin", "instagram", "facebook", "youtube", "tiktok", "reddit", "spotify",
    "stripe", "paypal", "mercadopago", "mercado pago", "shopify", "wordpress", "zapier", "make", "n8n", "infisical",
    "vaultwarden", "bitwarden", "1password", "cognee", "mcp",
}
# Capitalised words that are not names (acronyms, document words) in English and Spanish.
COMMON_WORDS = {
    "pr", "prs", "api", "apis", "url", "urls", "ui", "ux", "ci", "cd", "mvp", "ok", "ai", "ia", "llm", "llms", "sql",
    "json", "csv", "pdf", "yaml", "html", "css", "todo", "faq", "kpi", "okr", "okrs", "qa", "id", "ids", "dm", "eta",
    "instructions", "instrucciones", "notes", "notas", "note", "nota", "idea", "ideas", "task", "tasks", "tarea",
    "tareas", "bug", "bugs", "issue", "issues", "ticket", "tickets", "docs", "doc", "readme", "release", "deploy",
    "update", "summary", "resumen", "meeting", "reunión", "reunion", "plan", "roadmap", "backlog", "sprint", "status",
    "estado", "important", "importante", "nuevo", "nueva", "new", "old", "draft", "borrador", "rules", "reglas",
    "regla", "rule", "guide", "guía", "guia", "checklist", "workflow", "flujo", "setup", "config", "test", "tests",
    # languages, weekdays, months
    "english", "spanish", "inglés", "ingles", "español", "espanol", "portuguese", "portugués", "french", "german",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "lunes", "martes", "miércoles",
    "jueves", "viernes", "sábado", "domingo", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
}


# Protocols and technical terms: a topic, never a person or a project.
TECH_TERMS = {
    "oauth", "oidc", "openid", "jwt", "sso", "saml", "rls", "rbac", "acl", "dns", "tls", "ssl", "http", "https", "ssh",
    "vpn", "cdn", "smtp", "imap", "webhook", "webhooks", "cron", "funnel", "proxy", "reverse proxy", "gateway",
    "router", "backend", "frontend", "endpoint", "endpoints", "token", "tokens", "embedding", "embeddings", "rag",
    "prompt", "prompts", "agent", "agents", "server", "servidor", "client", "cliente", "database", "base de datos",
}
_TRAILER = re.compile(r"(?im)^\s*(?:co-authored-by|signed-off-by|reviewed-by|acked-by|tested-by|reported-by|"
                      r"suggested-by|change-id|[a-z]+(?:-[a-z]+)*-by)\s*:.*$")
_PERSON_CUE = re.compile(r"\b(?:manager|jefe|jefa|boss|partner|pareja|wife|husband|esposa|esposo|novia|novio|"
                         r"girlfriend|boyfriend|sister|hermana|brother|hermano|friend|amig[oa]|colleague|colega|"
                         r"coworker|compañer[oa]|mother|madre|mom|mamá|father|padre|dad|papá|son|hijo|daughter|hija|"
                         r"dog|perro|cat|gat[oa]|cousin|prim[oa]|uncle|t[ií][oa]|client[ea]?|cliente|mentor|"
                         r"met|conoció|named|llamad[oa]|se llama|told|dijo|said|asked|pidió|he|she|él|ella)\b", re.I)


def strip_trailers(text: str) -> str:
    """Drop git commit trailers (Co-authored-by: …) that pasted commit messages bring along."""
    return _TRAILER.sub("", text or "").strip()


def is_acronym(name: str) -> bool:
    """PR, ADC, RLS, OAuth, READMEs: all capitals, or a short word with capitals after the first letter."""
    w = str(name or "").strip()
    if " " in w or not w:
        return False
    core = w[:-1] if w.endswith("s") and len(w) > 2 and w[:-1].isupper() else w
    if core.isupper() and core.isalpha() and len(core) <= 6:
        return True
    return core.lower() in TECH_TERMS or (core[:-1].lower() in COMMON_WORDS if core.endswith("s") else False)


def is_tech(name: str) -> bool:
    n = " ".join(str(name or "").lower().split())
    return n in TECH_TERMS or is_acronym(name)


def _extra_tools() -> set[str]:
    import os
    return {t.strip().lower() for t in os.environ.get("MNEMOS_KNOWN_TOOLS", "").split(",") if t.strip()}


def is_tool(name: str) -> bool:
    n = " ".join(str(name or "").lower().split())
    return n in KNOWN_TOOLS or n in _extra_tools()


def is_common(name: str) -> bool:
    n = " ".join(str(name or "").lower().split())
    return n in COMMON_WORDS or (n.endswith("s") and n[:-1] in COMMON_WORDS)


def _camel(w: str) -> bool:
    return any(ch.isupper() for ch in w[1:]) and any(ch.islower() for ch in w)


def name_candidates(text: str, known: Iterable[str] = ()) -> list[str]:
    """Capitalised spans that may be names: not sentence-initial (a capital there proves nothing) unless the word is
    CamelCase or in `known` (names seen sentence-initially across several notes), and not a stop or common word,
    an acronym or technical term, or a known tool."""
    known_l = {k.lower() for k in known}
    out = []
    for m in _CAP.finditer(strip_trailers(text)):
        span = m.group(1)
        before = text[:m.start()].rstrip()
        words = [w for w in span.split() if w not in _STOP]
        if not before or before[-1] in ".!?:;\n-•*\"'(":
            if not words or not (_camel(words[0]) or words[0].lower() in known_l or span.lower() in known_l):
                continue
        span = " ".join(words)
        if span.lower().endswith("-by") or "-by " in span.lower():
            continue
        if (span and not is_common(span) and not is_tool(span) and not is_tech(span)
                and not all(is_common(w) or is_tech(w) for w in words)):
            out.append(span)
    return out


def repeated_initial_names(texts: Iterable[str], min_notes: int = 2) -> set[str]:
    """Words that open sentences in several notes and never appear in lower case: probably names (a project that is
    always the subject), not ordinary words ("Usar", "Siempre")."""
    texts = [strip_trailers(t) for t in texts]
    lower = set()
    for t in texts:
        lower |= set(re.findall(r"\b[a-záéíóúñ][\wáéíóúñ'-]+", t))
    seen: dict[str, int] = {}
    for t in texts:
        firsts = set(re.findall(r"(?:^|[.!?:;\n]\s*)([A-ZÁÉÍÓÚÑ][\wáéíóúñ'-]{2,})", t))
        for w in firsts:
            seen[w] = seen.get(w, 0) + 1
    return {w for w, n in seen.items() if n >= min_notes and w.lower() not in lower and w not in _STOP
            and not is_common(w) and not is_tool(w) and not is_tech(w)}


_KIND_HINT = [("person", re.compile(r"\b(?:manager|jefe|jefa|partner|pareja|wife|husband|esposa|esposo|sister|hermana|"
                                    r"brother|hermano|friend|amig[oa]|colleague|colega|works with|trabaja con|"
                                    r"mother|madre|father|padre|son|hijo|daughter|hija|dog|perro|cat|gat[oa])\b", re.I)),
              ("project", re.compile(r"\b(?:project|proyecto|app|side project|building|construyendo|startup|repo)\b", re.I)),
              ("org", re.compile(r"\b(?:works? at|trabaja en|company|empresa|team at|equipo de)\b", re.I)),
              ("tool", re.compile(r"\b(?:uses?|usa|stack|framework|library|tool|herramienta)\b", re.I)),
              ("place", re.compile(r"\b(?:lives in|vive en|moved to|se mudó a|from|de)\s+[A-Z]", re.I))]


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def guess_kind(text: str, tags: Iterable[str] = ()) -> str | None:
    for t in tags:
        if t.startswith("kind-") and t[5:] in KINDS:
            return t[5:]
    low = text.lower()
    if re.search(r"\b(prefer\w*|prefier\w*|likes?|le gusta|dislikes?|odia|always|siempre|never|nunca|wants? .* in)\b", low):
        return "preference"
    if re.search(r"\b(decid|chose|eligió|switched|cambió|moved .* to|migr)\w*", low):
        return "decision"
    if re.search(r"\b(goal|meta|objetivo|wants to|quiere|learning|aprendiendo|plans? to|planea)\b", low):
        return "goal"
    m = re.search(r"\b(?:works? with|trabaja con|uses?|usa|utiliza)\s+([\w.+ -]{2,30}?)(?=[\s,;.]|$)", text, re.I)
    if m and any(is_tool(" ".join(m.group(1).split()[:k])) for k in (1, 2, 3)):
        return "skill_fact"  # "works with ClickUp" is about a tool, not a person
    if re.search(r"\b(manager|jefe|partner|pareja|wife|husband|sister|hermana|brother|friend|works with|trabaja con)\b", low):
        return "relationship"
    if re.search(r"\b(project|proyecto|building|construyendo|stack|uses .* for)\b", low):
        return "project_state"
    if re.search(r"\b(lives|vive|works as|trabaja como|is a|es una?|born|nació|name is|se llama)\b", low):
        return "identity"
    # instructions and working habits ("PRs should be small", "no usar X", "always ask before")
    if re.search(r"\b(should|shouldn't|must|debe\w*|debería\w*|hay que|tiene que|no usar|don't|do not|avoid|evitar|"
                 r"prefer\w*|rather|mejor que|in (english|spanish|inglés|español)|format|formato|style|estilo|"
                 r"workflow|flujo|always|siempre|never|nunca|before|antes de|instead|en vez de|every|each|cada|"
                 r"todos los|daily|weekly|usually|suele|routine|rutina|first|primero|template|plantilla|wants?|quiere)\b",
                 low):
        return "preference"
    if re.search(r"\b(uses?|usa|uso|utiliza\w*|knows?|sabe|experienced? in|experiencia en|expert|maneja|"
                 r"works? (?:on|with)|trabaja (?:en|con)|maintains?|mantiene|owns?|administra|runs?)\b", low):
        return "skill_fact"
    if re.search(r"\b(is (?:now|currently)|está (?:ahora|en)|status|estado|deployed|desplegad\w*|launched|lanzad\w*|"
                 r"released|version|versión|v\d|in progress|en curso|pending|pendiente|done|terminad\w*)\b", low):
        return "project_state"
    return None


class Model:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript(SCHEMA)

    def db(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ---------------------------------------------------------------- entities
    def entities(self, status: str = "active") -> list[dict[str, Any]]:
        with self.db() as c:
            rows = c.execute("SELECT e.*, (SELECT COUNT(*) FROM facts f WHERE f.entity_id=e.id AND f.status='active') AS n "
                             "FROM entities e WHERE status=? ORDER BY pinned DESC, importance DESC, n DESC", (status,))
            return [{**dict(r), "aliases": json.loads(r["aliases"] or "[]"),
                     "summary_facts": json.loads(r["summary_facts"] or "[]")} for r in rows]

    def me(self, owner: str = "me") -> str:
        with self.db() as c:
            r = c.execute("SELECT id FROM entities WHERE kind='me' AND status='active'").fetchone()
            if r:
                return r["id"]
            eid = "me"
            c.execute("INSERT OR IGNORE INTO entities(id, name, kind, aliases, importance, created_at) VALUES (?,?,?,?,?,?)",
                      (eid, owner or "me", "me", json.dumps(["me", "yo", "I", "user", "usuario"]), 1.0, now()))
            return eid

    def resolve(self, name: str, kind: str | None = None, create: bool = True, batch: str | None = None) -> str | None:
        """Entity id for a name: exact name/alias (case-insensitive) or a new one."""
        n = " ".join(str(name or "").split()).strip(" .,:;'\"")[:60]
        if not n:
            return None
        if n.lower() in ("me", "yo", "i", "user", "usuario", "the user", "el usuario"):
            return self.me()
        low = n.lower()
        for e in self.entities():
            if e["name"].lower() == low or low in (a.lower() for a in e["aliases"]):
                return e["id"]
        if not create:
            return None
        if is_tool(n):
            kind = "tool"
        elif is_tech(n) and kind in ("person", "project", "org"):
            kind = "topic"
        eid = "e-" + uuid.uuid4().hex[:10]
        with self.db() as c:
            c.execute("INSERT INTO entities(id, name, kind, aliases, created_at, batch) VALUES (?,?,?,?,?,?)",
                      (eid, n, kind if kind in ENTITY_KINDS else "topic", "[]", now(), batch))
        return eid

    def add_alias(self, eid: str, alias: str) -> None:
        with self.db() as c:
            r = c.execute("SELECT aliases FROM entities WHERE id=?", (eid,)).fetchone()
            if r:
                al = json.loads(r["aliases"] or "[]")
                if alias not in al:
                    c.execute("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(al + [alias]), eid))

    def link_entity(self, text: str, about: str | None = None, batch: str | None = None) -> str:
        """Pick the entity a fact is about: [[Name]] > the extractor's 'about' > a known name in the text > a new
        capitalised name with a kind hint > me. Tools and background entities (the context's own org) named in
        passing are never the subject; a new person needs a person cue next to the name."""
        text = strip_trailers(text)
        w = _WIKI.findall(text)
        if w:
            return self.resolve(w[0], self._entity_kind(w[0], text), batch=batch) or self.me()
        if about and about.strip().lower() not in ("", "me", "user", "the user", "yo") and not is_common(about):
            return self.resolve(about, self._entity_kind(about, text), batch=batch) or self.me()
        low = text.lower()
        bg = {b.lower() for b in getattr(self, "background", ())}
        best = None
        for e in self.entities():
            if e["kind"] in ("me", "tool") or e["name"].lower() in bg:  # named in passing is not what it is about
                continue
            for nm in [e["name"], *e["aliases"]]:
                if len(nm) >= 3 and re.search(r"\b" + re.escape(nm.lower()) + r"\b", low):
                    if not best or len(nm) > best[1]:
                        best = (e["id"], len(nm))
        if best:
            return best[0]
        kind = self._kind_for(text)
        if kind in ("project", "person", "org"):
            caps = [c for c in name_candidates(text, getattr(self, "known_names", ())) if c.lower() not in bg]
            if kind == "person":
                caps = [c for c in caps if self._person_near(c, text)][:1]  # the name right after the cue
            if caps:
                return self.resolve(caps[-1], kind, batch=batch) or self.me()
        return self.me()

    @staticmethod
    def _person_near(name: str, text: str, words: int = 3) -> bool:
        """A person cue within a few words of the name ("my manager Laura", "Laura, mi jefa")."""
        i = text.find(name)
        if i < 0:
            return False
        left = " ".join(text[:i].split()[-words:])
        right = " ".join(text[i + len(name):].split()[:words])
        return bool(_PERSON_CUE.search(left) or _PERSON_CUE.search(right))

    def _entity_kind(self, name: str, text: str) -> str | None:
        if is_tool(name):
            return "tool"
        if is_tech(name):
            return "topic"
        if name.lower() in {b.lower() for b in getattr(self, "background", ())}:
            return "org"
        k = self._kind_for(text)
        return None if k == "person" and not self._person_near(name, text) else k

    @staticmethod
    def _kind_for(text: str) -> str | None:
        for k, rx in _KIND_HINT:
            if rx.search(text):
                return k
        return None

    # ---------------------------------------------------------------- facts (add-only)
    def add_fact(self, note_id: str, text: str, *, kind: str | None = None, about: str | None = None,
                 importance: float | None = None, durability: str = "long", expires_at: str | None = None,
                 origin: str = "user_said", source_app: str | None = None, quote: str | None = None,
                 confidence: float = 0.7, tags: Iterable[str] = (), batch: str | None = None,
                 recorded_at: str | None = None) -> dict[str, Any]:
        tags = list(tags)
        kind = kind if kind in KINDS else guess_kind(text, tags)
        eid = self.link_entity(text, about, batch)
        imp = importance if importance is not None else (0.8 if kind in ("identity", "preference", "relationship")
                                                         else 0.6 if kind else 0.5)
        if "pinned" in tags:
            imp = 1.0
        for t in tags:
            if t.startswith("until-") and re.fullmatch(r"\d{4}-\d\d-\d\d", t[6:]):
                expires_at, durability = t[6:], "short"
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO facts(note_id, text, entity_id, kind, importance, durability, expires_at, "
                      "valid_from, confidence, origin, source_app, quote, recorded_at, status, batch) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (note_id, text[:2000], eid, kind, imp, durability, expires_at, (recorded_at or now())[:10],
                       confidence, origin, source_app, (quote or "")[:300], recorded_at or now(), "active", batch))
            c.execute("UPDATE entities SET dirty=1 WHERE id=?", (eid,))
            c.execute("INSERT OR IGNORE INTO links(a, b, kind, created_at) VALUES (?,?,?,?)", (note_id, eid, "about", now()))
        return {"note_id": note_id, "entity_id": eid, "kind": kind}

    def fact(self, note_id: str) -> dict[str, Any] | None:
        with self.db() as c:
            r = c.execute("SELECT f.*, e.name AS entity, e.kind AS entity_kind FROM facts f LEFT JOIN entities e "
                          "ON e.id=f.entity_id WHERE note_id=?", (note_id,)).fetchone()
            return dict(r) if r else None

    def facts(self, entity_id: str | None = None, status: str | None = "active") -> list[dict[str, Any]]:
        q, p = "SELECT f.*, e.name AS entity, e.kind AS entity_kind FROM facts f LEFT JOIN entities e ON e.id=f.entity_id", []
        w = []
        if entity_id:
            w.append("f.entity_id=?")
            p.append(entity_id)
        if status:
            w.append("f.status=?")
            p.append(status)
        if w:
            q += " WHERE " + " AND ".join(w)
        q += " ORDER BY f.recorded_at DESC"
        with self.db() as c:
            return [dict(r) for r in c.execute(q, p)]

    def forget_note(self, note_id: str) -> None:
        """The note was deleted by the user: drop its row (history stays in the ledger)."""
        with self.db() as c:
            r = c.execute("SELECT entity_id FROM facts WHERE note_id=?", (note_id,)).fetchone()
            c.execute("DELETE FROM facts WHERE note_id=?", (note_id,))
            c.execute("DELETE FROM links WHERE a=? OR b=?", (note_id, note_id))
            if r:
                c.execute("UPDATE entities SET dirty=1 WHERE id=?", (r["entity_id"],))
            self._stale_profile(c, note_id)

    @staticmethod
    def _stale_profile(c: Any, note_id: str) -> None:
        """The stored L0 profile quotes this note: drop its text so the next memory_profile rebuilds it."""
        r = c.execute("SELECT fact_ids FROM profile WHERE id=1").fetchone()
        if r and note_id in (r["fact_ids"] or ""):
            c.execute("UPDATE profile SET text=NULL WHERE id=1")

    def set_fact(self, note_id: str, job: str, **fields: Any) -> None:
        """Change model fields of a fact (never the note), logged in `changes` so a job can be undone."""
        if not fields:
            return
        with self.db() as c:
            r = c.execute("SELECT * FROM facts WHERE note_id=?", (note_id,)).fetchone()
            if not r:
                return
            for k, v in fields.items():
                c.execute("INSERT INTO changes(ts, job, note_id, field, old, new) VALUES (?,?,?,?,?,?)",
                          (now(), job, note_id, k, json.dumps(r[k]), json.dumps(v)))
            c.execute(f"UPDATE facts SET {', '.join(f'{k}=?' for k in fields)} WHERE note_id=?", (*fields.values(), note_id))
            if "text" in fields:
                self._stale_profile(c, note_id)

    def undo_job(self, job: str) -> int:
        with self.db() as c:
            rows = c.execute("SELECT * FROM changes WHERE job=? ORDER BY id DESC", (job,)).fetchall()
            for r in rows:
                c.execute(f"UPDATE facts SET {r['field']}=? WHERE note_id=?", (json.loads(r["old"]), r["note_id"]))
            c.execute("DELETE FROM changes WHERE job=?", (job,))
        return len(rows)

    def touch(self, note_ids: Iterable[str]) -> None:
        ids = list(note_ids)
        if not ids:
            return
        with self.db() as c:
            c.executemany("UPDATE facts SET use_count=use_count+1, last_used_at=? WHERE note_id=?", [(now(), i) for i in ids])

    def not_relevant(self, note_id: str) -> None:
        with self.db() as c:
            c.execute("UPDATE facts SET not_relevant=not_relevant+1, importance=MAX(0.05, importance*0.6) WHERE note_id=?",
                      (note_id,))

    def confirm(self, note_id: str) -> None:
        with self.db() as c:
            c.execute("UPDATE facts SET confirmed_at=?, confidence=MIN(1.0, confidence+0.2) WHERE note_id=?", (now(), note_id))

    # ---------------------------------------------------------------- migration (dry run / apply / undo)
    def migrate(self, notes: list[dict[str, Any]], apply: bool = False, owner: str = "me",
                context: str | None = None) -> dict[str, Any]:
        """notes: memory_list items. Dry run: what would be linked (no write). Apply: add rows tagged with a batch.
        context: the context's name (e.g. a company), an org that is background to every note, never their subject."""
        known = {r["note_id"] for r in self.facts(status=None)}
        todo = [n for n in notes if n.get("id") and n["id"] not in known and str(n.get("text", "")).strip()]
        names = repeated_initial_names(str(n.get("text", "")) for n in notes)
        bg = [context] if context and context.lower() not in ("personal", "default", "shared", "me") else []
        if not apply:
            import tempfile  # the dry run works on a throw-away copy
            with tempfile.TemporaryDirectory() as td:
                probe = Model(Path(td) / "m.sqlite")
                probe.known_names, probe.background = names, bg
                probe.me(owner)
                for b in bg:
                    probe.resolve(b, "org")
                for e in self.entities():
                    probe.resolve(e["name"], e["kind"])
                kinds: dict[str, int] = {}
                for n in todo:
                    r = probe.add_fact(n["id"], n["text"], tags=n.get("tags") or [], origin="imported",
                                       source_app=n.get("source_app"))
                    kinds[r["kind"] or "unknown"] = kinds.get(r["kind"] or "unknown", 0) + 1
                ents = [{"name": e["name"], "kind": e["kind"], "facts": e["n"]} for e in probe.entities() if e["n"]]
            return {"dry_run": True, "notes": len(todo), "already": len(known), "kinds": kinds,
                    "entities": sorted(ents, key=lambda e: -e["facts"])[:40]}
        batch = "mig-" + uuid.uuid4().hex[:8]
        self.known_names, self.background = names, bg
        self.me(owner)
        for n in todo:
            self.add_fact(n["id"], n["text"], tags=n.get("tags") or [], origin="imported", source_app=n.get("source_app"),
                          batch=batch, recorded_at=str(n.get("created_at") or "") or None)
        return {"dry_run": False, "batch": batch, "migrated": len(todo)}

    def undo_migration(self, batch: str) -> dict[str, int]:
        if not re.fullmatch(r"mig-[0-9a-f]{8}", batch or ""):
            raise ValueError("unknown migration")
        with self.db() as c:
            n = c.execute("DELETE FROM facts WHERE batch=?", (batch,)).rowcount
            c.execute("DELETE FROM links WHERE a NOT IN (SELECT note_id FROM facts) AND kind='about'")
            e = c.execute("DELETE FROM entities WHERE batch=? AND id NOT IN (SELECT DISTINCT entity_id FROM facts "
                          "WHERE entity_id IS NOT NULL)", (batch,)).rowcount
        return {"facts": n, "entities": e}
