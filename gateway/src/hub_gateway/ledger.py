"""Mnemos' own registry of notes: provenance (and, later, history) that Cognee does not keep.

Cognee stores our provenance in `external_metadata`, but drops it on a full rebuild
(see docs/memory-editing.md) and keeps no versions. This small SQLite file, in the gateway
data dir (HUB_DATA_DIR, one per context), is the durable record. It holds note texts, so it
lives next to the rest of the instance data and is covered by the same backups.

The registry is best-effort: a failure here is logged and never blocks a memory operation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import sqlite3
import threading
from typing import Any

log = logging.getLogger("hub.ledger")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    data_id     TEXT,                 -- Cognee data id (NULL until known)
    dataset     TEXT NOT NULL,
    text_hash   TEXT NOT NULL,        -- sha256 of the current text
    context     TEXT NOT NULL,
    source_app  TEXT,
    login       TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS notes_data_id ON notes(data_id) WHERE data_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS notes_hash ON notes(dataset, text_hash);
"""

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    note    INTEGER NOT NULL,       -- notes.rowid (stable even if the Cognee id changes)
    text    TEXT NOT NULL,          -- the text BEFORE the change
    tags    TEXT NOT NULL DEFAULT '[]',
    action  TEXT NOT NULL,          -- update | delete | undone (text discarded by an undo)
    at      TEXT NOT NULL,
    by      TEXT
);
CREATE INDEX IF NOT EXISTS versions_note ON versions(note);
"""


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    def _migrate(self, c: sqlite3.Connection) -> None:
        cols = {r[1] for r in c.execute("PRAGMA table_info(notes)")}
        if "deleted_at" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN deleted_at TEXT")
        if "prev_ids" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN prev_ids TEXT NOT NULL DEFAULT ''")
        if "origin" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN origin TEXT")  # JSON: where a promoted note came from
        c.executescript(HISTORY_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ provenance
    def record_save(self, *, dataset: str, text: str, context: str, source_app: str | None,
                    login: str | None, tags: list[str], data_id: str | None = None,
                    origin: dict[str, Any] | None = None) -> None:
        ts = now()
        with self._lock, self._conn() as c:
            if data_id and c.execute("SELECT 1 FROM notes WHERE data_id=?", (data_id,)).fetchone():
                return  # Cognee deduplicated: same text, same note; keep the original provenance
            c.execute(
                "INSERT INTO notes(data_id, dataset, text_hash, context, source_app, login, tags, created_at, updated_at,"
                " origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data_id, dataset, text_hash(text), context, source_app, login,
                 json.dumps(tags, ensure_ascii=False), ts, ts,
                 json.dumps(origin, ensure_ascii=False) if origin else None),
            )

    def provenance(self, data_id: str, dataset: str, text: str | None = None) -> dict[str, Any] | None:
        """Provenance of a note. Binds a pending entry (saved before Cognee returned an id) by text hash."""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM notes WHERE data_id=?", (data_id,)).fetchone()
            if row is None and text is not None:
                row = c.execute(
                    "SELECT rowid AS rid, * FROM notes WHERE data_id IS NULL AND dataset=? AND text_hash=?"
                    " ORDER BY created_at LIMIT 1", (dataset, text_hash(text))).fetchone()
                if row is not None:
                    c.execute("UPDATE notes SET data_id=? WHERE rowid=?", (data_id, row["rid"]))
            return _public(row) if row is not None else None

    def record_change(self, data_id: str, *, text: str | None, by: str | None,
                      tags: list[str] | None = None, new_data_id: str | None = None,
                      id_changes: bool = False) -> None:
        """A note was corrected. If its Cognee id changes and the new one is unknown, the entry goes
        back to pending and is re-bound by text hash on the next listing."""
        with self._lock, self._conn() as c:
            sets, args = ["updated_at=?", "updated_by=?"], [now(), by]
            if text is not None:
                sets.append("text_hash=?"); args.append(text_hash(text))
            if tags is not None:
                sets.append("tags=?"); args.append(json.dumps(tags, ensure_ascii=False))
            if new_data_id or id_changes:
                sets.append("data_id=?"); args.append(new_data_id)
            c.execute(f"UPDATE notes SET {', '.join(sets)} WHERE data_id=?", (*args, data_id))

    # ------------------------------------------------------------------ history
    def _row(self, c: sqlite3.Connection, data_id: str) -> sqlite3.Row | None:
        row = c.execute("SELECT rowid AS rid, * FROM notes WHERE data_id=?", (data_id,)).fetchone()
        if row is None:  # an id a restored note had before (ids are UUIDs: no false substring hits)
            row = c.execute("SELECT rowid AS rid, * FROM notes WHERE instr(prev_ids, ?) > 0 ORDER BY rowid DESC",
                            (data_id,)).fetchone()
        return row

    def snapshot(self, *, data_id: str, dataset: str, context: str, text: str, tags: list[str],
                 action: str, by: str | None, created_at: str | None = None, source_app: str | None = None) -> None:
        """Keep the current text of a note before changing or deleting it (creates the entry if missing)."""
        ts = now()
        with self._lock, self._conn() as c:
            row = self._row(c, data_id)
            if row is None:  # note saved before the registry: adopt it with what Cognee knows
                cur = c.execute(
                    "INSERT INTO notes(data_id, dataset, text_hash, context, source_app, tags, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (data_id, dataset, text_hash(text), context, source_app,
                     json.dumps(tags, ensure_ascii=False), created_at or ts, created_at or ts))
                rid = cur.lastrowid
            else:
                rid = row["rid"]
            c.execute("INSERT INTO versions(note, text, tags, action, at, by) VALUES (?,?,?,?,?,?)",
                      (rid, text, json.dumps(tags, ensure_ascii=False), action, ts, by))
            if action == "delete":
                c.execute("UPDATE notes SET deleted_at=?, updated_at=?, updated_by=? WHERE rowid=?", (ts, ts, by, rid))

    def history(self, data_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as c:
            row = self._row(c, data_id)
            if row is None:
                return None
            vers = c.execute("SELECT rowid AS vid, * FROM versions WHERE note=? ORDER BY rowid DESC",
                             (row["rid"],)).fetchall()
        return {
            "id": data_id, "dataset": row["dataset"], "deleted": row["deleted_at"] is not None,
            "provenance": _public(row),
            "previous_versions": [{"version": i, "action": v["action"], "replaced_at": v["at"], "by": v["by"],
                                   "tags": json.loads(v["tags"]), "text": v["text"]}
                                  for i, v in zip(range(len(vers), 0, -1), vers)],
        }

    def last_version(self, data_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as c:
            row = self._row(c, data_id)
            if row is None:
                return None
            v = c.execute("SELECT rowid AS vid, * FROM versions WHERE note=? AND action != 'undone'"
                          " ORDER BY rowid DESC LIMIT 1",
                          (row["rid"],)).fetchone()
        if v is None:
            return None
        return {"vid": v["vid"], "text": v["text"], "tags": json.loads(v["tags"]), "action": v["action"],
                "dataset": row["dataset"], "deleted": row["deleted_at"] is not None}

    def drop_version(self, vid: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM versions WHERE rowid=?", (vid,))

    def restored(self, data_id: str, *, text: str, by: str | None, new_data_id: str | None) -> None:
        """A deleted note was saved again: back to active, re-bound by id or (pending) by text hash."""
        with self._lock, self._conn() as c:
            row = self._row(c, data_id)
            if row is None:
                return
            prev = " ".join(x for x in (row["prev_ids"], row["data_id"]) if x)
            c.execute("UPDATE notes SET deleted_at=NULL, data_id=?, prev_ids=?, text_hash=?, updated_at=?,"
                      " updated_by=? WHERE rowid=?", (new_data_id, prev, text_hash(text), now(), by, row["rid"]))


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source_app": row["source_app"],
        "saved_by": row["login"],
        "saved_in_context": row["context"],
        "saved_at": row["created_at"],
        "updated_at": row["updated_at"] if row["updated_at"] != row["created_at"] else None,
        "updated_by": row["updated_by"],
        **({"promoted_from": json.loads(row["origin"])} if "origin" in row.keys() and row["origin"] else {}),
    }


def safe(fn, *args, **kwargs):
    """Call a ledger method without ever breaking the memory operation."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        log.exception("ledger: %s failed", getattr(fn, "__name__", fn))
        return None
