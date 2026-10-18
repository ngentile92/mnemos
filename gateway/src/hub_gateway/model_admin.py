"""Memory model for the Docker hub (0.10): run inside a context's gateway container.

    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin sync            # dry run: what would be linked
    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin sync --apply    # link notes (prints a batch)
    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin undo-sync mig-xxxxxxxx
    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin consolidate [--ollama URL --model NAME]
    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin undo-job cons-xxxxxxxx
    docker compose exec gateway-<ctx> python -m hub_gateway.model_admin profile | entities | digest

The model is a sidecar SQLite (<data_dir>/model.sqlite) next to the ledger: Cognee notes are never rewritten or
deleted by it. The gateway uses it (ranking, memory_profile) only with HUB_MEMORY_MODEL=1. Cognee's /remember does not
return the note id, so new notes are linked by `sync --apply` (idempotent; run it nightly before `consolidate`).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from typing import Any

from .local_model import Model


def model_path(data_dir: str) -> str:
    return os.path.join(data_dir, "model.sqlite")


async def own_notes(server: Any, limit: int = 500) -> list[dict[str, Any]]:
    from fastmcp import Client
    async with Client(server) as c:
        r = await c.call_tool("memory_list", {"limit": limit, "include_shared": False})
    data = r.structured_content if r.structured_content is not None else json.loads(r.content[0].text)
    return [i for i in data.get("items", []) if i.get("editable", True) and i.get("text")]


class OllamaJSON:
    """Minimal LLM for summaries (optional): Ollama's /api/generate in JSON mode."""

    def __init__(self, url: str, model: str) -> None:
        self.url, self.model = url.rstrip("/"), model

    async def json(self, prompt: str) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(self.url + "/api/generate", json={"model": self.model, "prompt": prompt, "format": "json",
                                                               "stream": False, "options": {"temperature": 0}})
            r.raise_for_status()
            return json.loads(r.json().get("response") or "{}")


async def run(args: argparse.Namespace, server: Any = None, data_dir: str | None = None) -> dict[str, Any]:
    from . import local_consolidate as lc
    if server is None or data_dir is None:
        from .app import build_server
        from .config import Settings
        settings = dataclasses.replace(Settings.from_env(), dev_no_auth=True)  # admin, inside the container only
        server, data_dir = build_server(settings), settings.data_dir
    m = Model(model_path(data_dir))
    if args.cmd == "sync":
        from . import contexts as _c
        return m.migrate(await own_notes(server), apply=args.apply, owner=str(getattr(_c, "OWNER", "") or "me"))
    if args.cmd == "undo-sync":
        return m.undo_migration(args.batch)
    if args.cmd == "consolidate":
        llm = OllamaJSON(args.ollama, args.model) if args.ollama and args.model else None
        return await lc.consolidate(m, llm)
    if args.cmd == "undo-job":
        if not args.job.startswith("cons-"):
            raise SystemExit("unknown job")
        return {"undone": m.undo_job(args.job)}
    if args.cmd == "profile":
        return {"profile": lc.profile_text(m) or lc.build_profile(m)[0]}
    if args.cmd == "entities":
        return {"entities": [{k: e[k] for k in ("name", "kind", "n", "summary")} for e in m.entities() if e["n"]]}
    if args.cmd == "digest":
        return {"items": lc.digest(m)}
    raise SystemExit(f"unknown command {args.cmd}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m hub_gateway.model_admin", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--apply", action="store_true")
    u = sub.add_parser("undo-sync"); u.add_argument("batch")
    c = sub.add_parser("consolidate"); c.add_argument("--ollama"); c.add_argument("--model")
    j = sub.add_parser("undo-job"); j.add_argument("job")
    for n in ("profile", "entities", "digest"):
        sub.add_parser(n)
    return p


def main(argv: list[str] | None = None) -> None:
    out = asyncio.run(run(parser().parse_args(argv)))
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
