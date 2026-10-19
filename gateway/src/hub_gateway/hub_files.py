"""0.13 prep: move one hub context from Cognee to simple memory (Markdown files + git). Run inside its gateway container.

    python -m hub_gateway.hub_files preview   # writes <memory>.preview/ + report, changes nothing
    python -m hub_gateway.hub_files apply     # writes the context's folders in the shared memory repo (same note ids)

Then set HUB_MEMORY_STORE=files for that context and restart it. Undo: unset HUB_MEMORY_STORE (Cognee was never
modified). Cognee can stay on as an optional search index.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .memmigrate import _report_md, plan
from .memfiles import MemoryRepo


async def read_cognee(settings) -> dict[str, list[dict[str, Any]]]:
    from .contexts import get_context
    from .memory import CogneeClient, DatasetMap, MemoryScope, item_summary
    ctx = get_context(settings.context)
    dm = DatasetMap.load(settings.datasets_file)
    sc = MemoryScope(ctx, dm)
    cog = CogneeClient(settings.cognee_url, settings.cognee_api_key)
    flags: dict[str, dict] = {}
    led = Path(settings.data_dir) / "ledger.sqlite"
    if led.is_file():
        con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
        try:
            for did, pinned, obs, tags in con.execute("SELECT data_id, pinned, obsolete_at, tags FROM notes"):
                flags[did] = {"pinned": bool(pinned), "obsolete": obs is not None, "tags": json.loads(tags or "[]")}
        except sqlite3.Error:
            pass
        con.close()
    out: dict[str, list[dict[str, Any]]] = {}
    for name, ds_id in sc.editable():
        notes = []
        for d in await cog.list_data(ds_id):
            it = item_summary(d, name)
            text = await cog.raw_text(ds_id, it["id"]) or ""
            if not text.strip():
                continue
            f = flags.get(it["id"], {})
            notes.append({"id": it["id"], "text": text.strip(), "created_at": it["created_at"],
                          "updated_at": it["created_at"], "source_app": it["source_app"],
                          "tags": it["tags"] or f.get("tags") or [], "pinned": f.get("pinned", False),
                          "past": f.get("obsolete", False), "kind": None, "importance": 0.5})
        if notes:
            out[name] = notes
    return out


def main(argv: list[str] | None = None) -> int:
    from .app import hub_memory_dir
    from .config import Settings
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "preview"
    settings = Settings.from_env()
    p = plan(asyncio.run(read_cognee(settings)))
    mem = Path(hub_memory_dir(settings))
    if action == "preview":
        out = Path(str(mem) + ".preview") / settings.context
        if out.exists():
            shutil.rmtree(out)
        MemoryRepo(out).import_files(p["files"], p["ids"], p["meta"], "Migration preview", "mnemos")
        (out / "report.md").write_text(_report_md(p["report"], f"preview ({settings.context})"))
        print(json.dumps({**p["report"], "path": str(out)}, indent=2))
        return 0
    if action == "apply":
        repo = MemoryRepo(mem)
        clash = [r for r in p["files"] if (mem / r).exists()]
        if clash:
            print(f"refusing: {len(clash)} files already exist in {mem} (e.g. {clash[0]})", file=sys.stderr)
            return 1
        repo.import_files(p["files"], p["ids"], p["meta"],
                          f"Migrated {p['report']['notes']} notes from Cognee ({settings.context})", "mnemos")
        print(json.dumps({"status": "applied", **p["report"], "next": "set HUB_MEMORY_STORE=files and restart"}))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
