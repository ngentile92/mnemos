#!/usr/bin/env python3
"""Export / import memory as a folder of Markdown files, in gbrain's page format.

Each note becomes one file: YAML frontmatter (`type`, `title`, `date`, `tags`, plus a `mnemos:` block with
id, dataset and source app) and the note text as the body. That is what `gbrain export` writes and
`gbrain import <dir>` reads, so memory can move between Mnemos and gbrain, or just be kept as plain files.

  .venv/bin/python3 scripts/memory_markdown.py export <out-dir> [--dataset personal ...]
  .venv/bin/python3 scripts/memory_markdown.py import <dir> --dataset personal [--yes]

Export writes <out-dir>/<dataset>/<date>-<title-slug>-<id8>.md (all datasets by default; never deletes).
Import reads every *.md under <dir> (gbrain pages or files from an export) into ONE dataset; without --yes it
only shows what it would save. Text already in the dataset is skipped. Imported notes are extracted to the graph
in the background (or by the nightly cognify). Runs on the host as hub-admin (see scripts/memory_admin.py);
the output contains your memory in clear text: keep it private.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MAX_TEXT = 20_000
MAX_TAGS = 5
TIMELINE_MARK = "<!-- timeline -->"


def slugify(text: str, max_len: int = 50) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:max_len].rstrip("-") or "note"


def title_of(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80].strip()
    return "note"


def _meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("externalMetadata") or item.get("external_metadata") or {}
    if isinstance(meta, list):
        meta = meta[0] if meta and isinstance(meta[0], dict) else {}
    return meta if isinstance(meta, dict) else {}


def to_markdown(item: dict[str, Any], text: str, dataset: str) -> tuple[str, str]:
    """(relative path, file content) for one Cognee data item."""
    meta = _meta(item)
    created = str(item.get("createdAt") or item.get("created_at") or "")[:19]
    day = created[:10] or "undated"
    title = title_of(text)
    fm: dict[str, Any] = {"type": "note", "title": title}
    if created:
        fm["date"] = created if created.endswith("Z") or "+" in created else created + "Z"
    tags = [str(t) for t in (meta.get("tags") or meta.get("node_set") or []) if str(t).strip()]
    if tags:
        fm["tags"] = tags
    fm["mnemos"] = {k: v for k, v in {
        "id": str(item.get("id")), "dataset": dataset, "source_app": meta.get("source_app"),
        "context": meta.get("hub_context"), "saved_at": meta.get("saved_at")}.items() if v}
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    rel = f"{dataset}/{day}-{slugify(title)}-{str(item.get('id'))[:8]}.md"
    return rel, f"---\n{head}\n---\n\n{text.rstrip()}\n"


def from_markdown(content: str) -> tuple[dict[str, Any], str]:
    """(frontmatter, text) of a gbrain page or exported note. The gbrain timeline section is kept as text."""
    fm: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        parts = content.split("\n---", 1)
        if len(parts) == 2:
            loaded = yaml.safe_load(parts[0][3:]) or {}
            if isinstance(loaded, dict):
                fm = loaded
            body = parts[1].lstrip("-").lstrip("\n")
    body = body.replace(TIMELINE_MARK, "Timeline:").strip()
    title = str(fm.get("title") or "").strip()
    first = title_of(body).strip().lower()
    if title and not isinstance(fm.get("mnemos"), dict) and not first.startswith(title.lower()[:80].strip()):
        body = f"# {title}\n\n{body}"  # gbrain keeps the title only in the frontmatter
    return fm, body


def import_plan(folder: Path, existing_hashes: set[str]) -> list[dict[str, Any]]:
    plan = []
    for p in sorted(folder.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(folder).parts):
            continue
        fm, text = from_markdown(p.read_text(encoding="utf-8", errors="replace"))
        rel = str(p.relative_to(folder))
        if not text.strip():
            plan.append({"file": rel, "skip": "empty"}); continue
        if len(text) > MAX_TEXT:
            plan.append({"file": rel, "skip": f"longer than {MAX_TEXT} characters"}); continue
        h = hashlib.sha256(text.strip().encode()).hexdigest()
        if h in existing_hashes:
            plan.append({"file": rel, "skip": "already in the dataset"}); continue
        existing_hashes.add(h)
        tags = fm.get("tags") or []
        tags = [str(t).strip()[:40] for t in (tags if isinstance(tags, list) else [tags]) if str(t).strip()][:MAX_TAGS]
        date = fm.get("date")
        plan.append({"file": rel, "text": text, "tags": tags,
                     "metadata": {"source_app": "import:markdown", "imported_from": rel[:200],
                                  "original_date": str(date)[:40] if date else None, "tags": tags,
                                  "saved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}})
    return plan


def main() -> int:
    from memory_admin import BASE, admin_client, read_env  # same login and safety checks

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("folder")
    ap.add_argument("--dataset", action="append", help="dataset name (repeatable for export; one for import)")
    ap.add_argument("--yes", action="store_true", help="import: save for real (without it, dry run)")
    ap.add_argument("--url", default=BASE)
    args = ap.parse_args()

    ids = json.loads((ROOT / "config" / "cognee-datasets.json").read_text())["datasets"]
    names = args.dataset or (sorted(ids) if args.action == "export" else [])
    unknown = [n for n in names if n not in ids]
    if unknown:
        raise SystemExit(f"unknown dataset(s): {unknown} (options: {', '.join(sorted(ids))})")
    c = admin_client(args.url.rstrip("/"), read_env(ROOT / ".env"))
    folder = Path(args.folder)

    def items_of(ds_id: str) -> list[dict[str, Any]]:
        r = c.get(f"/api/v1/datasets/{ds_id}/data")
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []

    def text_of(ds_id: str, did: str) -> str | None:
        r = c.get(f"/api/v1/datasets/{ds_id}/data/{did}/raw")
        return r.content.decode("utf-8", "replace") if r.status_code == 200 else None

    if args.action == "export":
        n = 0
        for name in names:
            for it in items_of(ids[name]):
                text = text_of(ids[name], str(it.get("id")))
                if text is None:
                    print(f"skip {it.get('id')}: could not read", file=sys.stderr); continue
                rel, content = to_markdown(it, text, name)
                out = folder / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(content, encoding="utf-8")
                n += 1
        print(f"exported {n} notes to {folder}")
        return 0

    if len(names) != 1:
        raise SystemExit("import needs exactly one --dataset")
    ds_id = ids[names[0]]
    existing = {hashlib.sha256((text_of(ds_id, str(i.get("id"))) or "").strip().encode()).hexdigest()
                for i in items_of(ds_id)}
    plan = import_plan(folder, existing)
    todo = [p for p in plan if "text" in p]
    for p in plan:
        print(f"{'SKIP ' + p['skip'] if 'skip' in p else 'SAVE'}  {p['file']}")
    if not args.yes:
        print(f"\n{len(todo)} to import into {names[0]} (dry run: nothing saved; add --yes)")
        return 0
    for p in todo:
        files = [("raw_data", (None, p["text"])), ("datasetId", (None, ds_id)), ("run_in_background", (None, "true")),
                 ("external_metadata", (None, json.dumps([{k: v for k, v in p["metadata"].items() if v}],
                                                          ensure_ascii=False)))]
        files += [("node_set", (None, t)) for t in p["tags"]]
        r = c.post("/api/v1/remember", files=files)
        if r.status_code >= 300:
            print(f"FAILED {p['file']}: HTTP {r.status_code}", file=sys.stderr)
            return 1
    print(f"imported {len(todo)} notes into {names[0]}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
