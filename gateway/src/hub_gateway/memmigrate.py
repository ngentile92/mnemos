"""Move an existing home from the notes store (SQLite) to simple memory (Markdown files + git).

preview  writes the would-be files to <home>/migration-preview/ plus report.md / report.json. Changes nothing else.
apply    writes <home>/memory/ (same note ids, so search, history and links keep working), one commit, and turns the
         files store on. The SQLite store is never modified.
undo     turns the files store off again. Notes added or edited in the files since `apply` are copied back into the
         SQLite store first, so nothing is lost either way.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .memfiles import (KIND_FILE, PROFILE, PROFILE_MAX, TOPICS, MemoryRepo, active, bullet_lines,
                       join_front, kind_for, new_file, now_iso, set_active, slugify)

PAST = "No longer true"
STATE = "migration.json"


def _contexts(home: Path) -> tuple[list[str], list[str]]:
    """(dataset names, context names) of this home, read from its contexts.yaml without changing the process."""
    from .contexts import load_config, parse_contexts
    _owner, ctxs = parse_contexts(load_config(home / "contexts.yaml"))
    datasets = ["shared"] + [d for c in ctxs.values() for d in c.own_dataset_names]
    return datasets, list(ctxs)


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def read_notes(home: Path) -> dict[str, list[dict[str, Any]]]:
    """{dataset: [note]} from the SQLite store, with ledger flags and Labs kinds when present (read-only)."""
    from .localstore import dataset_id
    datasets, ctxs = _contexts(home)
    flags: dict[str, dict] = {}
    kinds: dict[str, dict] = {}
    for ctx in ctxs:
        con = _ro(home / "data" / ctx / "ledger.sqlite")
        if con:
            try:
                for r in con.execute("SELECT data_id, pinned, obsolete_at FROM notes"):
                    flags[r["data_id"]] = {"pinned": bool(r["pinned"]), "obsolete": r["obsolete_at"] is not None}
            except sqlite3.Error:
                pass
            con.close()
        con = _ro(home / "data" / ctx / "model.sqlite")
        if con:
            try:
                for r in con.execute("SELECT note_id, kind, status, importance FROM facts"):
                    kinds[r["note_id"]] = {"kind": r["kind"], "status": r["status"], "importance": r["importance"]}
            except sqlite3.Error:
                pass
            con.close()
    out: dict[str, list[dict[str, Any]]] = {}
    for name in datasets:
        con = _ro(home / "data" / "notes" / f"{dataset_id(name)}.sqlite")
        if not con:
            continue
        try:
            rows = con.execute("SELECT id, text, created_at, updated_at, meta, node_set FROM data "
                               "ORDER BY created_at").fetchall()
        except sqlite3.Error:
            rows = []
        con.close()
        notes = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except ValueError:
                meta = {}
            meta = meta if isinstance(meta, dict) else {}
            tags = list(meta.get("tags") or [])
            f, k = flags.get(r["id"], {}), kinds.get(r["id"], {})
            notes.append({"id": r["id"], "text": " ".join(str(r["text"]).split()) if "\n" not in str(r["text"]).strip()
                          else str(r["text"]).strip(), "created_at": r["created_at"], "updated_at": r["updated_at"],
                          "source_app": meta.get("source_app"), "tags": tags, "pinned": f.get("pinned", False),
                          "past": bool(f.get("obsolete")) or k.get("status") in ("superseded", "obsolete", "expired"),
                          "kind": k.get("kind"), "importance": k.get("importance") or 0.5})
        if notes:
            out[name] = notes
    return out


def _file_for(name: str, n: dict[str, Any]) -> str:
    tags = [str(t).lower() for t in n["tags"]]
    for t in tags:
        if t.startswith(("topic-", "topic:")) and slugify(t[6:]):
            return f"{name}/{TOPICS}/{slugify(t[6:])}.md"
    if n["pinned"] or "pinned" in tags:
        return f"{name}/{PROFILE}"
    kind = n["kind"] or next((t[5:] for t in tags if t.startswith("kind-")), None)
    if kind in KIND_FILE:
        return f"{name}/{KIND_FILE[kind]}"
    return f"{name}/{TOPICS}/notes.md"


def plan(notes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """{files: {rel: text}, ids: {rel: [id per bullet]}, meta: {id: {...}}, report}"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for name, ns in notes.items():
        for n in ns:
            groups.setdefault(_file_for(name, n), []).append(n)
    # profile: the most important current notes, at most PROFILE_MAX; the rest go to topics/about-me.md
    for rel in [r for r in groups if r.endswith("/" + PROFILE)]:
        name = rel.split("/")[0]
        cur = [n for n in groups[rel] if not n["past"]]
        cur.sort(key=lambda n: (not n["pinned"], -(n["importance"] or 0.5)))
        keep = {n["id"] for n in cur[:PROFILE_MAX]}
        over = [n for n in groups[rel] if n["id"] not in keep and not n["past"]]
        if over:
            groups[rel] = [n for n in groups[rel] if n["id"] in keep or n["past"]]
            groups.setdefault(f"{name}/{TOPICS}/about-me.md", []).extend(over)
    files, ids, meta = {}, {}, {}
    for rel, ns in sorted(groups.items()):
        name = rel.split("/")[0]
        kind, title = kind_for(rel)
        fm, body = new_file(kind, title, name)
        cur = [n for n in ns if not n["past"]]
        past = [n for n in ns if n["past"]]
        lines, order = list(body), []
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        for n in cur:
            lines += bullet_lines(n["text"])
            order.append(n["id"])
        if past:
            lines += ([""] if cur else []) + [f"## {PAST}"]
            for n in past:
                bl = bullet_lines(n["text"])
                bl[0] = "- ~~" + bl[0][2:] + "~~" if len(bl) == 1 else bl[0]
                lines += bl
                order.append(n["id"])
        srcs = sorted({str(n["source_app"]) for n in ns if n.get("source_app")})[:8]
        fm["sources"] = srcs
        fm["updated"] = now_iso()
        files[rel] = join_front(fm, lines)
        ids[rel] = order
        for n in ns:
            meta[n["id"]] = {"created_at": n["created_at"], "updated_at": n["updated_at"],
                             "source_app": n["source_app"], "tags": n["tags"]}
    rep = {"notes": sum(len(v) for v in notes.values()), "files": {r: len(v) for r, v in ids.items()},
           "past": sum(1 for v in notes.values() for n in v if n["past"]), "datasets": sorted(notes)}
    return {"files": files, "ids": ids, "meta": meta, "report": rep}


def _report_md(rep: dict[str, Any], target: str) -> str:
    out = [f"# Simple memory: migration {target}", "",
           f"{rep['notes']} notes from {len(rep['datasets'])} space(s) → {len(rep['files'])} files "
           f"({rep['past']} kept as history under \"{PAST}\").", ""]
    for rel, n in rep["files"].items():
        out.append(f"- `{rel}`: {n} notes")
    out += ["", "Nothing was changed in the current memory. Apply from Mnemos → Memory, or `mnemos-local migrate apply`."]
    return "\n".join(out) + "\n"


def _write(repo_root: Path, p: dict[str, Any], message: str) -> MemoryRepo:
    repo = MemoryRepo(repo_root)
    repo.import_files(p["files"], p["ids"], p["meta"], message, "mnemos")
    return repo


def preview(home: str | Path) -> dict[str, Any]:
    home = Path(home)
    p = plan(read_notes(home))
    out = home / "migration-preview"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    _write(out / "memory", p, "Migration preview")
    (out / "report.json").write_text(json.dumps(p["report"], indent=2, ensure_ascii=False))
    (out / "report.md").write_text(_report_md(p["report"], "preview"))
    return {**p["report"], "path": str(out), "active": active(home)}


def apply(home: str | Path) -> dict[str, Any]:
    home = Path(home)
    if active(home):
        return {"status": "already", "active": True}
    mem = home / "memory"
    if mem.exists() and any(mem.glob("*/*.md")):
        if (home / "data" / STATE).exists():  # a migration undone earlier: start over from the notes store
            shutil.move(str(mem), str(home / f"memory.before-{now_iso().replace(':', '')}"))
        else:
            raise SystemExit(f"{mem} already has files; move it away first")
    p = plan(read_notes(home))
    _write(mem, p, f"Migrated {p['report']['notes']} notes from the notes store")
    (home / "data").mkdir(exist_ok=True)
    (home / "data" / STATE).write_text(json.dumps({"applied_at": now_iso(), **p["report"]}, indent=2))
    set_active(home, True)
    return {"status": "applied", **p["report"], "active": True}


def undo(home: str | Path) -> dict[str, Any]:
    """Back to the notes store. New and edited notes from the files go back into SQLite first."""
    home = Path(home)
    if not active(home):
        return {"status": "already", "active": False}
    from .localstore import LocalNoteStore, dataset_id
    datasets, _ = _contexts(home)
    repo = MemoryRepo(home / "memory")
    store = LocalNoteStore(home / "data" / "notes", None, {dataset_id(n): n for n in datasets})
    added = edited = 0

    async def run() -> None:
        nonlocal added, edited
        for name in datasets:
            ds = dataset_id(name)
            for b in repo.scan(name):
                if b["past"]:
                    continue
                old = await store.raw_text(ds, b["id"])
                if old is None:  # new since apply: same id, so links and history still point at it
                    m = b["meta"]
                    meta = {"source_app": m.get("source_app"), "tags": m.get("tags") or []}
                    ts = m.get("created_at") or now_iso()
                    with store._lock, store._db(ds) as c:
                        c.execute("INSERT OR IGNORE INTO data VALUES (?,?,?,?,?,?)",
                                  (b["id"], b["text"], ts, m.get("updated_at") or ts,
                                   json.dumps(meta, ensure_ascii=False), json.dumps(m.get("tags") or [])))
                    added += 1
                elif " ".join(old.split()) != " ".join(b["text"].split()):
                    await store.update(ds, b["id"], b["text"])
                    edited += 1
    asyncio.run(run())
    set_active(home, False)
    return {"status": "undone", "active": False, "copied_back": {"added": added, "edited": edited}}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="mnemos-local migrate")
    ap.add_argument("action", choices=["preview", "apply", "undo"])
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    from .local import home_dir
    home = home_dir(a.home)
    fn = {"preview": preview, "apply": apply, "undo": undo}[a.action]
    print(json.dumps(fn(home), indent=2, ensure_ascii=False))
    return 0
