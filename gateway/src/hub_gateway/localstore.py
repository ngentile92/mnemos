"""Local memory backend: notes in SQLite, no Cognee, no Docker (single-process / Desktop mode).

Same interface the tools use on CogneeClient (remember / list_data / raw_text / update / delete_data / recall),
so every tool, the ledger, history/undo and the local search index work unchanged. The note is the source of
truth; there is no knowledge graph here (memory_search uses the local index; mode="graph" falls back to a
plain keyword scan).

Isolation: one SQLite file per dataset, and each context's store only opens the dataset ids it is allowed to
(its own + shared + bridges), mirroring Cognee's 403. A bug in a tool cannot read another context's file.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .memory import MAX_TEXT_CHARS, MemoryError_

NS = uuid.UUID("6d6e656d-6f73-4c6f-8361-6c0000000000")


def dataset_id(name: str) -> str:
    """Stable id for a dataset name (local mode has no Cognee to hand out ids)."""
    return str(uuid.uuid5(NS, name))


SCHEMA = """CREATE TABLE IF NOT EXISTS data (
  id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}', node_set TEXT NOT NULL DEFAULT '[]')"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class LocalNoteStore:
    def __init__(self, root: str | Path, allowed_ids: set[str] | None, names: dict[str, str] | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed = allowed_ids  # None = unrestricted (tests/tools); a context always passes its set
        self.names = names or {}
        self._lock = threading.Lock()

    def _db(self, ds: str) -> sqlite3.Connection:
        if self.allowed is not None and ds not in self.allowed:
            raise MemoryError_("dataset fuera del alcance de este contexto (HTTP 403)")
        if not re.fullmatch(r"[0-9a-f-]{36}", ds):
            raise MemoryError_("dataset id inválido")
        c = sqlite3.connect(self.root / f"{ds}.sqlite")
        c.row_factory = sqlite3.Row
        c.execute(SCHEMA)
        return c

    async def remember(self, text: str, dataset_id: str, node_set: list[str] | None = None,
                       run_in_background: bool = True, metadata: dict[str, Any] | None = None) -> Any:
        if not text.strip():
            raise MemoryError_("texto vacío")
        if len(text) > MAX_TEXT_CHARS:
            raise MemoryError_(f"texto demasiado largo (máx {MAX_TEXT_CHARS} caracteres)")
        meta = {k: v for k, v in (metadata or {}).items() if v not in (None, "", [])}
        with self._lock, self._db(dataset_id) as c:
            same = c.execute("SELECT id FROM data WHERE text=?", (text,)).fetchone()  # Cognee dedups the same way
            if same:
                nid = same["id"]
            else:
                nid, ts = str(uuid.uuid4()), _now()
                c.execute("INSERT INTO data VALUES (?,?,?,?,?,?)",
                          (nid, text, ts, ts, json.dumps(meta, ensure_ascii=False), json.dumps(node_set or [])))
        return {"status": "completed", "items": [{"id": nid}]}

    async def list_data(self, dataset_id: str) -> list[dict[str, Any]]:
        with self._lock, self._db(dataset_id) as c:
            rows = c.execute("SELECT id, created_at, updated_at, meta FROM data ORDER BY created_at DESC").fetchall()
        return [{"id": r["id"], "createdAt": r["created_at"], "updatedAt": r["updated_at"], "mimeType": "text/plain",
                 "externalMetadata": json.loads(r["meta"])} for r in rows]

    async def raw_text(self, dataset_id: str, data_id: str, max_bytes: int = 4 * MAX_TEXT_CHARS) -> str | None:
        try:
            with self._lock, self._db(dataset_id) as c:
                r = c.execute("SELECT text FROM data WHERE id=?", (data_id,)).fetchone()
        except MemoryError_:
            return None
        return r["text"][:max_bytes] if r else None

    async def update(self, dataset_id: str, data_id: str, text: str, node_set: list[str] | None = None) -> Any:
        if not text.strip():
            raise MemoryError_("texto vacío")
        if len(text) > MAX_TEXT_CHARS:
            raise MemoryError_(f"texto demasiado largo (máx {MAX_TEXT_CHARS} caracteres)")
        with self._lock, self._db(dataset_id) as c:
            sets, args = "text=?, updated_at=?", [text, _now()]
            if node_set is not None:
                sets += ", node_set=?"
                args.append(json.dumps(node_set))
            if c.execute(f"UPDATE data SET {sets} WHERE id=?", (*args, data_id)).rowcount == 0:
                raise MemoryError_("nota no encontrada (HTTP 404)")
        return {"status": "completed", "data_id": data_id}

    async def delete_data(self, dataset_id: str, data_id: str) -> None:
        with self._lock, self._db(dataset_id) as c:
            if c.execute("DELETE FROM data WHERE id=?", (data_id,)).rowcount == 0:
                raise MemoryError_("nota no encontrada (HTTP 404)")

    async def recall(self, query: str, dataset_ids: list[str], top_k: int = 10, search_type: str | None = None) -> Any:
        """No graph here: keyword overlap over the allowed datasets (memory_search normally uses the local index)."""
        if not dataset_ids:
            raise MemoryError_("sin datasets: el gateway nunca consulta sin alcance explícito")
        words = {w for w in re.findall(r"\w{3,}", query.lower())}
        scored = []
        for ds in dataset_ids:
            with self._lock, self._db(ds) as c:
                for r in c.execute("SELECT id, text FROM data"):
                    s = sum(1 for w in words if w in r["text"].lower())
                    if s:
                        scored.append((s, ds, r["text"]))
        scored.sort(key=lambda x: -x[0])
        return [{"dataset_id": ds, "dataset_name": self.names.get(ds), "text": t} for _, ds, t in scored[:top_k]]
