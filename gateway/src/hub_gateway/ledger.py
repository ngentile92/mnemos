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
        """Hook for later schema additions."""

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ provenance
    def record_save(self, *, dataset: str, text: str, context: str, source_app: str | None,
                    login: str | None, tags: list[str], data_id: str | None = None) -> None:
        ts = now()
        with self._lock, self._conn() as c:
            if data_id and c.execute("SELECT 1 FROM notes WHERE data_id=?", (data_id,)).fetchone():
                return  # Cognee deduplicated: same text, same note; keep the original provenance
            c.execute(
                "INSERT INTO notes(data_id, dataset, text_hash, context, source_app, login, tags, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (data_id, dataset, text_hash(text), context, source_app, login,
                 json.dumps(tags, ensure_ascii=False), ts, ts),
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


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source_app": row["source_app"],
        "saved_by": row["login"],
        "saved_in_context": row["context"],
        "saved_at": row["created_at"],
        "updated_at": row["updated_at"] if row["updated_at"] != row["created_at"] else None,
        "updated_by": row["updated_by"],
    }


def safe(fn, *args, **kwargs):
    """Call a ledger method without ever breaking the memory operation."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        log.exception("ledger: %s failed", getattr(fn, "__name__", fn))
        return None
