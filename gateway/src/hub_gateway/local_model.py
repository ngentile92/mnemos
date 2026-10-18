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
    if re.search(r"\b(manager|jefe|partner|pareja|wife|husband|sister|hermana|brother|friend|works with|trabaja con)\b", low):
        return "relationship"
    if re.search(r"\b(project|proyecto|building|construyendo|stack|uses .* for)\b", low):
        return "project_state"
    if re.search(r"\b(lives|vive|works as|trabaja como|is a|es una?|born|nació|name is|se llama)\b", low):
        return "identity"
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
        capitalised name with a kind hint > me."""
        w = _WIKI.findall(text)
        if w:
            return self.resolve(w[0], self._kind_for(text), batch=batch) or self.me()
        if about and about.strip().lower() not in ("", "me", "user", "the user", "yo"):
            return self.resolve(about, self._kind_for(text), batch=batch) or self.me()
        low = text.lower()
        best = None
        for e in self.entities():
            if e["kind"] == "me":
                continue
            for nm in [e["name"], *e["aliases"]]:
                if len(nm) >= 3 and re.search(r"\b" + re.escape(nm.lower()) + r"\b", low):
                    if not best or len(nm) > best[1]:
                        best = (e["id"], len(nm))
        if best:
            return best[0]
        kind = self._kind_for(text)
        if kind in ("project", "person", "org"):
            caps = [m for m in _CAP.findall(text) if m.split()[0] not in _STOP]
            if caps:
                return self.resolve(caps[-1] if kind != "person" else caps[-1], kind, batch=batch) or self.me()
        return self.me()

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
    def migrate(self, notes: list[dict[str, Any]], apply: bool = False, owner: str = "me") -> dict[str, Any]:
        """notes: memory_list items. Dry run: what would be linked (no write). Apply: add rows tagged with a batch."""
        known = {r["note_id"] for r in self.facts(status=None)}
        todo = [n for n in notes if n.get("id") and n["id"] not in known and str(n.get("text", "")).strip()]
        if not apply:
            import tempfile  # the dry run works on a throw-away copy
            with tempfile.TemporaryDirectory() as td:
                probe = Model(Path(td) / "m.sqlite")
                probe.me(owner)
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
