"""Local search over the context's notes, without Cognee's graph and without API keys.

Keeps a small SQLite index (search.sqlite in HUB_DATA_DIR) of the raw note texts, synced from Cognee's
note list (only new notes are downloaded). Two signals:
  * keyword: SQLite FTS5 / BM25, accent- and case-insensitive (always available);
  * semantic: embeddings from a local Ollama model, if HUB_EMBED_URL and HUB_EMBED_MODEL are set
    (e.g. http://host.docker.internal:11434 and paraphrase-multilingual). Nothing leaves the machine.
`hybrid` fuses both rankings (reciprocal rank fusion); without embeddings it is keyword only.
"""

from __future__ import annotations

import array
import asyncio
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger("hub.search")

STOP = set("""a al algo ante con de del desde donde el en entre es esta este esto la las le lo los mas me mi mis no o
para pero por que quien quién qué cual cuál como cómo cuando cuándo se ser si sin sobre su sus un una uno unos unas y ya
yo tu te hay son fue the a an and are as at be by for from has have how i in is it of on or that the this to was what
when where which who why with my do does did""".split())
RRF_K = 60


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def query_terms(q: str) -> list[str]:
    terms = [t for t in re.findall(r"[a-z0-9]+", fold(q)) if len(t) > 1 and t not in STOP]
    return list(dict.fromkeys(terms))


def fts_query(q: str) -> str | None:
    terms = query_terms(q)
    if not terms:
        return None
    # prefix match makes "lanzamiento"/"lanza", "Brisa"/"Brisa's" match; OR keeps recall, BM25 ranks
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


def _pack(v: list[float]) -> bytes:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", (x / norm for x in v)).tobytes()


def _unpack(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


class OllamaEmbedder:
    def __init__(self, url: str, model: str, timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.model, self.timeout, self.transport = url.rstrip("/"), model, timeout, transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport, trust_env=False) as c:
            r = await c.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]

    @classmethod
    def from_env(cls) -> "OllamaEmbedder | None":
        url, model = os.environ.get("HUB_EMBED_URL"), os.environ.get("HUB_EMBED_MODEL")
        return cls(url, model) if url and model else None


class LocalIndex:
    def __init__(self, path: str, embedder: OllamaEmbedder | None = None, sync_interval: float = 60.0) -> None:
        self.path = path
        self.embedder = embedder
        self.sync_interval = sync_interval
        self._synced: dict[str, float] = {}
        self._lock = threading.Lock()
        self._alock = asyncio.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS docs (data_id TEXT PRIMARY KEY, dataset TEXT NOT NULL,
                    text TEXT NOT NULL, created_at TEXT, emb BLOB, emb_model TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                    data_id UNINDEXED, dataset UNINDEXED, text, tokenize='unicode61 remove_diacritics 2');
            """)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ write
    def put(self, data_id: str, dataset: str, text: str, created_at: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM docs_fts WHERE data_id=?", (data_id,))
            if created_at is None:
                row = c.execute("SELECT created_at FROM docs WHERE data_id=?", (data_id,)).fetchone()
                created_at = row[0] if row else None
            c.execute("INSERT OR REPLACE INTO docs(data_id, dataset, text, created_at) VALUES (?,?,?,?)",
                      (data_id, dataset, text, created_at))
            c.execute("INSERT INTO docs_fts(data_id, dataset, text) VALUES (?,?,?)", (data_id, dataset, text))

    def remove(self, data_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM docs WHERE data_id=?", (data_id,))
            c.execute("DELETE FROM docs_fts WHERE data_id=?", (data_id,))

    def ids(self, dataset: str) -> set[str]:
        with self._lock, self._conn() as c:
            return {r[0] for r in c.execute("SELECT data_id FROM docs WHERE dataset=?", (dataset,))}

    async def sync(self, dataset: str, list_items: Callable[[], Awaitable[list[dict[str, Any]]]],
                   raw_text: Callable[[str], Awaitable[str | None]], force: bool = False) -> None:
        """Bring the index of one dataset in line with Cognee (downloads only notes it does not have)."""
        async with self._alock:
            if not force and time.monotonic() - self._synced.get(dataset, -1e9) < self.sync_interval:
                return
            items = await list_items()
            remote = {str(d.get("id")): d for d in items}
            have = self.ids(dataset)
            for gone in have - set(remote):
                self.remove(gone)
            for did in set(remote) - have:
                text = await raw_text(did)
                if text is not None:
                    d = remote[did]
                    self.put(did, dataset, text, d.get("createdAt") or d.get("created_at"))
            await self._embed_missing()
            self._synced[dataset] = time.monotonic()

    def invalidate(self, dataset: str | None = None) -> None:
        if dataset is None:
            self._synced.clear()
        else:
            self._synced.pop(dataset, None)

    async def _embed_missing(self) -> None:
        if not self.embedder:
            return
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT data_id, text FROM docs WHERE emb IS NULL OR emb_model != ?",
                             (self.embedder.model,)).fetchall()
        for i in range(0, len(rows), 16):
            batch = rows[i:i + 16]
            try:
                vecs = await self.embedder.embed([r["text"][:4000] for r in batch])
            except Exception as exc:  # noqa: BLE001 — semantic is optional; keyword keeps working
                log.warning("embeddings unavailable: %s", type(exc).__name__)
                return
            with self._lock, self._conn() as c:
                for r, v in zip(batch, vecs):
                    c.execute("UPDATE docs SET emb=?, emb_model=? WHERE data_id=?",
                              (_pack(v), self.embedder.model, r["data_id"]))

    # ------------------------------------------------------------------ read
    def keyword(self, query: str, datasets: list[str], k: int) -> list[str]:
        q = fts_query(query)
        if not q or not datasets:
            return []
        marks = ",".join("?" * len(datasets))
        with self._lock, self._conn() as c:
            try:
                rows = c.execute(f"SELECT data_id FROM docs_fts WHERE docs_fts MATCH ? AND dataset IN ({marks})"
                                 " ORDER BY bm25(docs_fts) LIMIT ?", (q, *datasets, k)).fetchall()
            except sqlite3.OperationalError:
                return []
        return [r[0] for r in rows]

    async def semantic(self, query: str, datasets: list[str], k: int) -> list[str]:
        if not self.embedder or not datasets:
            return []
        try:
            qv = _unpack(_pack((await self.embedder.embed([query]))[0]))
        except Exception as exc:  # noqa: BLE001
            log.warning("embeddings unavailable: %s", type(exc).__name__)
            return []
        marks = ",".join("?" * len(datasets))
        with self._lock, self._conn() as c:
            rows = c.execute(f"SELECT data_id, emb FROM docs WHERE emb IS NOT NULL AND emb_model=? AND dataset IN ({marks})",
                             (self.embedder.model, *datasets)).fetchall()
        scored = sorted(((sum(a * b for a, b in zip(qv, _unpack(r["emb"]))), r["data_id"]) for r in rows),
                        reverse=True)
        return [d for _, d in scored[:k]]

    async def search(self, query: str, datasets: list[str], k: int, mode: str) -> list[dict[str, Any]]:
        kw = self.keyword(query, datasets, k * 3) if mode in ("keyword", "hybrid") else []
        sem = await self.semantic(query, datasets, k * 3) if mode in ("semantic", "hybrid") else []
        fused: dict[str, float] = {}
        for ranking in (kw, sem):
            for r, did in enumerate(ranking, 1):
                fused[did] = fused.get(did, 0.0) + 1.0 / (RRF_K + r)
        top = sorted(fused, key=lambda d: -fused[d])[:k]
        if not top:
            return []
        with self._lock, self._conn() as c:
            rows = {r["data_id"]: r for r in c.execute(
                f"SELECT * FROM docs WHERE data_id IN ({','.join('?' * len(top))})", top)}
        return [{"id": d, "dataset": rows[d]["dataset"], "created_at": rows[d]["created_at"],
                 "text": rows[d]["text"][:2000], "match": "+".join(
                     n for n, rk in (("keyword", kw), ("semantic", sem)) if d in rk)} for d in top if d in rows]
