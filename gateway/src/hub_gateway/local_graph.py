"""Own lightweight graph for the Graph view (no Cognee in the app): the local model reads the notes, 12 at a time, and
writes (subject, relation, object) triples, kept in <home>/data/<ctx>/graph.sqlite per note. Built in the background
when the Graph view is opened, only for notes that are new or changed; triples of deleted notes go away. Without a
local model the view keeps the name/tag graph (local_ui.build_graph). Only Ollama or the built-in model are used:
notes never go to an API or another app for this.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import logging
import re
import time
import sqlite3
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("hub_gateway.local_graph")
TRIPLES = ("Extract the entities and the relations stated in these notes (people, organisations, projects, products, "
           "tools, places, topics). Use short canonical names; call the person who wrote the notes \"me\". "
           'Reply JSON only: {"triples": [["subject", "relation", "object", <note number>], ...], '
           '"types": {"<entity>": "person|org|project|tool|place|topic"}}\n\n')
PER_CALL = 12
MAX_NOTES = 600   # per background run; the next Graph visit continues (most important notes first)
VERSION = "2"     # bump when the prompt changes: notes are re-read in the background
TYPES = ("person", "org", "project", "tool", "place", "topic")
ME = {"me", "i", "myself", "user", "the user", "author", "the author", "yo", "el usuario", "usuario", "mí", "mi"}


def _h(t: str) -> str:
    return hashlib.sha1((VERSION + t).encode("utf-8", "ignore")).hexdigest()[:16]


class Graph:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.task: asyncio.Task | None = None
        self.progress: dict[str, Any] = {}
        self.recent: collections.deque = collections.deque(maxlen=40)  # (seq, subject, relation, object) just found
        self.seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, h TEXT);
                CREATE TABLE IF NOT EXISTS triples (note TEXT, s TEXT, r TEXT, o TEXT);
                CREATE INDEX IF NOT EXISTS t_note ON triples(note);
                CREATE TABLE IF NOT EXISTS types (note TEXT, name TEXT, type TEXT);
                CREATE INDEX IF NOT EXISTS ty_note ON types(note);""")

    def _c(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def todo(self, items: list[dict]) -> list[dict]:
        live = {it["id"]: it for it in items if not it.get("obsolete")}
        with self._lock, self._c() as c:
            have = dict(c.execute("SELECT id, h FROM notes").fetchall())
            for gone in set(have) - set(live):
                c.execute("DELETE FROM notes WHERE id=?", (gone,))
                c.execute("DELETE FROM triples WHERE note=?", (gone,))
                c.execute("DELETE FROM types WHERE note=?", (gone,))
        return [it for i, it in live.items() if have.get(i) != _h(str(it.get("text", "")))][:MAX_NOTES]

    def prune(self, live_ids: set[str]) -> int:
        """Drop rows of notes that are gone (deleted or cleaned up). Cheap; runs on every graph view, LLM or not."""
        with self._lock, self._c() as c:
            gone = [i for (i,) in c.execute("SELECT DISTINCT note FROM triples UNION SELECT id FROM notes") if i not in live_ids]
            for g in gone:
                c.execute("DELETE FROM notes WHERE id=?", (g,))
                c.execute("DELETE FROM triples WHERE note=?", (g,))
                c.execute("DELETE FROM types WHERE note=?", (g,))
        return len(gone)

    def drop_junk(self) -> int:
        """Delete links to generic words / languages / identifiers (they are hidden anyway; this keeps the file lean)."""
        with self._lock, self._c() as c:
            bad = [(n, a, r, b) for n, a, r, b in c.execute("SELECT note, s, r, o FROM triples") if junk(a) or junk(b)]
            c.executemany("DELETE FROM triples WHERE note=? AND s=? AND r=? AND o=?", bad)
        return len(bad)

    def store(self, notes: list[dict], triples: list[tuple[str, str, str, str]],
              types: list[tuple[str, str, str]] | None = None) -> None:
        with self._lock, self._c() as c:
            for n in notes:
                c.execute("DELETE FROM triples WHERE note=?", (n["id"],))
                c.execute("DELETE FROM types WHERE note=?", (n["id"],))
                c.execute("INSERT OR REPLACE INTO notes VALUES (?,?)", (n["id"], _h(str(n.get("text", "")))))
            c.executemany("INSERT INTO triples VALUES (?,?,?,?)", triples)
            c.executemany("INSERT INTO types VALUES (?,?,?)", types or [])

    def triples(self) -> list[tuple[str, str, str, str]]:
        with self._lock, self._c() as c:
            return c.execute("SELECT note, s, r, o FROM triples").fetchall()

    def types(self) -> dict[str, str]:
        """entity (casefolded) → its most frequent type."""
        with self._lock, self._c() as c:
            rows = c.execute("SELECT name, type FROM types").fetchall()
        cnt: collections.Counter = collections.Counter((norm(n), t) for n, t in rows)
        best: dict[str, tuple[int, str]] = {}
        for (name, t), n in cnt.items():
            if n > best.get(name, (0, ""))[0]:
                best[name] = (n, t)
        return {k: v[1] for k, v in best.items()}

    async def build(self, items: list[dict], llm: Any) -> None:
        todo = self.todo(items)
        self.progress = {"done": 0, "total": len(todo), "started": time.time()}
        for i in range(0, len(todo), PER_CALL):
            part = todo[i:i + PER_CALL]
            chunk = "\n".join(f"{j}. {str(n.get('text', ''))[:400]}" for j, n in enumerate(part))
            try:
                res = await llm.json(TRIPLES + chunk)
                got = res.get("triples") or []
                typ = res.get("types") if isinstance(res.get("types"), dict) else {}
            except Exception as e:  # noqa: BLE001 — try these notes again next time
                log.warning("graph batch skipped: %s", type(e).__name__)
                continue
            rows = []
            for x in got:
                if not (isinstance(x, list) and len(x) >= 3 and all(isinstance(y, str) and y.strip() for y in x[:3])):
                    continue
                k = x[3] if len(x) > 3 and isinstance(x[3], int) and 0 <= x[3] < len(part) else None
                if k is None:  # no note number: the note naming both ends, else the object, else the subject
                    low = [str(n.get("text", "")).lower() for n in part]
                    score = [2 * (x[2].lower() in t) + (x[0].lower() in t) for t in low]
                    k = max(range(len(part)), key=lambda j: score[j]) if part and max(score) else None
                if k is not None:
                    rows.append((part[k]["id"], " ".join(x[0].split())[:60], " ".join(x[1].split())[:40],
                                 " ".join(x[2].split())[:60]))
            trows = []
            for note, a, _r, b in rows:
                for name in (a, b):
                    t = str(typ.get(name) or "").strip().lower()
                    if t in TYPES:
                        trows.append((note, name, t))
            self.store(part, rows, trows)
            for _note, a, r, b in rows:
                if not (junk(a) or junk(b)):
                    self.seq += 1
                    self.recent.append((self.seq, a, r, b))
            self.progress["done"] = min(len(todo), i + PER_CALL)

    def start(self, items: list[dict], llm: Any) -> bool:
        """Build in the background (one run at a time). True when it is (still) building."""
        if self.task and not self.task.done():
            return True
        if not llm or not self.todo(items):
            return False
        self.task = asyncio.create_task(self.build(items, llm))
        return True


_G: dict[str, Graph] = {}


def get(home: Path, ctx: str) -> Graph:
    p = Path(home) / "data" / ctx / "graph.sqlite"
    if str(p) not in _G:
        _G[str(p)] = Graph(p)
    return _G[str(p)]


def norm(name: str) -> str:
    return " ".join(name.casefold().replace("’", "'").split()).removeprefix("the ")


# Words a model happily turns into "entities" that mean nothing on a map for humans (English + Spanish).
GENERIC = set("""
app apps application aplicación aplicacion aplicaciones project projects proyecto proyectos user users usuario usuarios
system sistema data datos information información informacion note notes nota notas thing things cosa cosas tool tools
herramienta herramientas file files archivo archivos document documento documentos task tasks tarea tareas idea ideas
meeting reunión reunion code código codigo feature features función funcion function web website sitio página pagina page
platform plataforma service servicio product producto client cliente preferences preferencias settings configuración
configuracion config version versión today hoy time tiempo work trabajo personal memory memoria model modelo chat email
mail api database server servidor team equipo company empresa language idioma lenguaje english inglés ingles spanish
español espanol castellano portuguese portugués french francés german alemán italian italiano something algo everything
todo nothing nada people gente persona person problem problema solution solución question pregunta answer respuesta
list lista item items goal goals objetivo objetivos plan plans step steps paso pasos change cambio cambios update
""".split()) | {"base de datos", "global preferences", "user preferences", "the app", "la app", "la aplicación"}
_LANG = re.compile(r"^(idioma|lengua|language|lenguaje)\s+\S+$|^\S+\s+(language|idioma)$")
_IDENT = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")          # GLOBAL_PREFERENCES, user_id: code identifiers
_DATE = re.compile(r"^[\d\s/.:,-]+$")


def junk(name: str) -> bool:
    """A generic word, a language, a code identifier, a number or a date: not a node anyone wants to see."""
    k = norm(name)
    for art in ("el ", "la ", "los ", "las ", "un ", "una ", "a ", "an "):
        k = k.removeprefix(art)
    return (len(k) < 2 or k in GENERIC or bool(_LANG.match(k) or _IDENT.match(k) or _DATE.match(k)))


_PRE = ("proyecto ", "project ", "el proyecto ", "the project ")
_SUF = (" project", " proyecto", " app", " inc", " inc.", " ltd", " llc", " team", " hq", " sa", " s.a.")
_TLD = re.compile(r"\.(com|info|io|ai|app|net|org|dev|co|es|ar)$")


def base(k: str) -> str:
    """The name without the decorations people add around it: "Nimbus project", "Nimbus.info",
    "Nimbus-E" and "proyecto Nimbus" are all Nimbus."""
    k0 = k
    for _ in range(2):
        for p in _PRE:
            k = k.removeprefix(p)
        for sfx in _SUF:
            if k.endswith(sfx) and len(k) > len(sfx) + 2:
                k = k[: -len(sfx)]
        k = _TLD.sub("", k)
        k = re.sub(r"-[a-z0-9]$", "", k)
        k = k.strip(" -_.")
    return k if len(k) >= 3 else k0


def tidy(names: dict[str, int]) -> dict[str, str]:
    """{key: canonical key} merges by rule (no model needed): decorated names onto their base name, and people whose
    first name is a short form of the other's with the same surname ("Ana Lopez" = "Anabel Lopez").
    The canonical one is the plain base name when it is used, else the most mentioned."""
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for k in names:
        groups[base(k)].append(k)
    out: dict[str, str] = {}
    for b, ks in groups.items():
        top = b if b in names else max(ks, key=lambda x: (names[x], -len(x)))
        for k in ks:
            if k != top:
                out[k] = top
    people = [k for k in names if k not in out and len(k.split()) >= 2]
    by_last: dict[str, list[str]] = collections.defaultdict(list)
    for k in people:
        by_last[k.split()[-1]].append(k)
    for ks in by_last.values():
        ks.sort(key=lambda x: (-names[x], len(x)))
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                fa, fb = a.split()[0], b.split()[0]
                if b not in out and min(len(fa), len(fb)) >= 3 and (fa.startswith(fb) or fb.startswith(fa)) \
                        and len(a.split()) == len(b.split()):
                    out[b] = out.get(a, a)
    return out


def _loose(name: str) -> str:
    return "".join(ch for ch in norm(name) if ch.isalnum())


def me_names(owner: str | None) -> set[str]:
    out = set(ME)
    if owner and owner.strip() and owner.strip().lower() not in ("me", "the owner"):
        out.add(norm(owner))
        out.add(norm(owner.split()[0]))
    return out


_VEC: dict[str, list[float]] = {}  # entity name → unit embedding (names are short; kept for the process)


_PAIRS: dict[tuple, list[tuple[int, int]]] = {}


def _similar(keys: list[str], threshold: float) -> list[tuple[int, int]]:
    ck = (tuple(keys), threshold)
    if ck in _PAIRS:
        return _PAIRS[ck]
    vs = [_VEC[k] for k in keys]
    try:
        import numpy as np
        m = np.asarray(vs, dtype="float32")
        ii, jj = np.where(np.triu(m @ m.T, 1) >= threshold)
        out = list(zip(ii.tolist(), jj.tolist()))
    except ImportError:  # pure Python: fewer names
        n = min(len(vs), 150)
        out = [(i, j) for i in range(n) for j in range(i + 1, n) if sum(a * b for a, b in zip(vs[i], vs[j])) >= threshold]
    if len(_PAIRS) > 20:
        _PAIRS.clear()
    _PAIRS[ck] = out
    return out


async def aliases(names: dict[str, int], types: dict[str, str], embedder: Any = None, threshold: float = 0.9,
                  limit: int = 300) -> dict[str, str]:
    """{entity key: canonical key}. Same letters ignoring spaces/punctuation ("mnemos-hub" = "Mnemos hub"), and, when
    an embedding model is there, names whose embeddings are ≥ threshold apart with the same (or unknown) type.
    The canonical one is the most mentioned."""
    keys = sorted(names, key=lambda k: -names[k])[:limit]
    parent = {k: k for k in keys}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def join(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            keep, drop = (ra, rb) if (names[ra], -len(ra)) >= (names[rb], -len(rb)) else (rb, ra)
            parent[drop] = keep
    loose: dict[str, str] = {}
    for k in keys:
        lk = _loose(k)
        if lk and lk in loose:
            join(loose[lk], k)
        else:
            loose.setdefault(lk, k)
    if embedder and len(keys) > 1:
        try:
            from .local_engine import embed_as
            miss = [k for k in keys if k not in _VEC]
            if miss:
                for k, v in zip(miss, await embed_as(embedder, miss, "sim")):
                    n = (sum(x * x for x in v) ** 0.5) or 1.0
                    _VEC[k] = [x / n for x in v]
            for a, b in _similar(keys, threshold):
                ti, tj = types.get(keys[a]), types.get(keys[b])
                if not (ti and tj and ti != tj):
                    join(keys[a], keys[b])
        except Exception:  # noqa: BLE001 — merging is a nicety; the graph works without it
            log.warning("alias merge skipped", exc_info=True)
    return {k: find(k) for k in keys if find(k) != k}


def clusters(nodes: list[str], edges: list[tuple[str, str, int]], rounds: int = 12) -> dict[str, int]:
    """Label propagation (weighted, deterministic): groups of entities that are mostly linked to each other."""
    nb: dict[str, collections.Counter] = {n: collections.Counter() for n in nodes}
    for a, b, w in edges:
        if a in nb and b in nb:
            nb[a][b] += w
            nb[b][a] += w
    lab = {n: i for i, n in enumerate(nodes)}
    for _ in range(rounds):
        changed = False
        for n in nodes:
            if not nb[n]:
                continue
            score: collections.Counter = collections.Counter()
            for m, w in nb[n].items():
                score[lab[m]] += w
            best = min(score, key=lambda x: (-score[x], x))
            if best != lab[n] and score[best] > score.get(lab[n], 0):
                lab[n], changed = best, True
        if not changed:
            break
    size = collections.Counter(lab.values())
    order = {c: i for i, (c, _n) in enumerate(sorted(size.items(), key=lambda x: (-x[1], x[0])))}
    return {n: order[lab[n]] for n in nodes}


def view(items: list[dict], triples: list[tuple[str, str, str, str]], max_nodes: int = 80, max_edges: int = 200,
         types: dict[str, str] | None = None, merge: dict[str, str] | None = None, me: set[str] | None = None,
         show_me: bool = False) -> dict:
    """Graph view data from model triples: typed nodes (with the spaces they appear in), directed labelled edges
    (subject → object), clusters. The author ("me", the owner's name) is one node, hidden unless show_me, so the
    graph shows the people and projects around you instead of a star around yourself."""
    types, merge, me = types or {}, merge or {}, me if me is not None else set(ME)
    live = {it["id"]: it for it in items if not it.get("obsolete")}
    canon: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def key(name: str) -> str | None:
        k = norm(name)
        if k in me:
            return "me"
        if junk(name):
            return None
        k = merge.get(k, k)
        k = merge.get(k, k)
        if k in me:
            return "me"
        canon[k][name] += 1
        return k
    notes_of: dict[str, set[str]] = collections.defaultdict(set)
    pairs: collections.Counter = collections.Counter()
    rels: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for note, s, r, o in triples:
        if note not in live:
            continue
        a, b = key(s), key(o)
        if a is None or b is None:
            continue
        if not show_me and "me" in (a, b):
            for k in (a, b):
                if k != "me":
                    notes_of[k].add(note)
            continue
        notes_of[a].add(note)
        notes_of[b].add(note)
        if a != b:
            pairs[(a, b)] += 1
            rels[(a, b)][" ".join(r.split()).lower()] += 1
    rank = {it["id"]: (bool(it.get("pinned")), bool(it.get("manual"))) for it in live.values()}
    weight = {k: len(v) + sum(2 * p + m for p, m in (rank[n] for n in v)) for k, v in notes_of.items()}
    keep = sorted(notes_of, key=lambda k: (-(k == "me"), -weight[k], k))[:max_nodes]
    ks = set(keep)
    edges = [(e, w) for e, w in pairs.most_common() if e[0] in ks and e[1] in ks][:max_edges]
    cl = clusters(keep, [(a, b, w) for (a, b), w in edges])
    used = {n for k in keep for n in sorted(notes_of[k])[:12]}

    def label(k: str) -> str:
        if k == "me":
            return "Me"
        plain = [n for n, _c in canon[k].most_common() if norm(n) == k]
        return plain[0] if plain else canon[k].most_common(1)[0][0]

    def spaces(k: str) -> list[str]:
        return sorted({str(live[n].get("space") or "") for n in notes_of[k]} - {""})
    return {"notes": len(live), "source": "model",
            "nodes": [{"id": k, "label": label(k), "kind": "me" if k == "me" else "name",
                       "type": "person" if k == "me" else types.get(k, "other"), "count": len(notes_of[k]),
                       "spaces": spaces(k), "cluster": cl.get(k, -1), "notes": sorted(notes_of[k])[:12]} for k in keep],
            "edges": [{"source": a, "target": b, "weight": w, "label": rels[(a, b)].most_common(1)[0][0],
                       "relations": [f"{label(a)} → {r} → {label(b)}" for r, _n in rels[(a, b)].most_common(3)]}
                      for (a, b), w in edges],
            "texts": {i: str(live[i].get("text", ""))[:600] for i in used}}
