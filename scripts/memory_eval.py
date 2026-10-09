#!/usr/bin/env python3
"""Retrieval eval: load a fixed corpus into THROW-AWAY datasets, run fixed queries through the real gateway
tools (in-process, no OAuth) and report hit@1 / hit@5 / MRR / latency per search mode.

  .venv/bin/python3 scripts/memory_eval.py                       # default modes, corpus in eval/
  .venv/bin/python3 scripts/memory_eval.py --modes graph --keep  # keep the datasets (prints a --reuse file)
  .venv/bin/python3 scripts/memory_eval.py --reuse /tmp/eval-ds.json

`avg_chars` is how much text each search returns: a search that returns everything always "hits".
A query hits at rank r when the r-th result contains one of its `expect` strings (case and accents ignored).
Queries with `expect: []` have no answer in the corpus: they are only used by the answer eval (abstention).
Runs on the host as hub-admin (see scripts/memory_admin.py). It never touches your real datasets: it creates
`zz_eval_*` datasets and deletes them at the end (unless --keep). Loading runs extraction synchronously, so the
first run takes a few minutes with a local model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def rank_of(results: list[str], expect: list[str]) -> int | None:
    """1-based rank of the first result containing any expected string."""
    exp = [norm(e) for e in expect]
    for i, text in enumerate(results, 1):
        t = norm(text)
        if any(e in t for e in exp):
            return i
    return None


def score(ranks: list[int | None], latencies: list[float], chars: list[int] | None = None) -> dict[str, float]:
    n = len(ranks) or 1
    return {
        "queries": len(ranks),
        "hit@1": round(sum(1 for r in ranks if r == 1) / n, 3),
        "hit@5": round(sum(1 for r in ranks if r and r <= 5) / n, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        "p50_ms": round(statistics.median(latencies) * 1000) if latencies else 0,
        # how much text the agent has to read per query (a search that returns everything always "hits")
        "avg_chars": round(statistics.mean(chars)) if chars else 0,
    }


async def eval_answers(m: Any, queries: list[dict[str, Any]]) -> dict[str, Any]:
    """memory_answer: answerable queries must be answered right AND cited; unanswerable ones must say unknown."""
    ok = cited = abstained = wrong = 0
    lat, fails = [], []
    answerable = [q for q in queries if q["expect"]]
    for q in queries:
        t = time.perf_counter()
        out = _data(await m.call_tool("memory_answer", {"question": q["q"], "include_shared": False}))
        lat.append(time.perf_counter() - t)
        if q["expect"]:
            right = out["known"] and rank_of([out["answer"]], q["expect"]) == 1
            ok += right
            cited += bool(right and rank_of([c["text"] for c in out["citations"]], q["expect"]))
            if not right:
                fails.append(q["q"])
        else:
            abstained += not out["known"]
            wrong += bool(out["known"])
            if out["known"]:
                fails.append(f"(should be unknown) {q['q']}: {out['answer'][:80]}")
    n_a, n_u = len(answerable) or 1, (len(queries) - len(answerable)) or 1
    return {"answer_accuracy": round(ok / n_a, 3), "cited_correctly": round(cited / n_a, 3),
            "abstention_on_unknown": round(abstained / n_u, 3), "hallucinated_on_unknown": wrong,
            "p50_ms": round(statistics.median(lat) * 1000) if lat else 0, "fails": fails}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _data(r: Any) -> Any:
    return r.structured_content if r.structured_content is not None else json.loads(r.content[0].text)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from fastmcp import Client

    from hub_gateway.app import build_server
    from hub_gateway.audit import Audit
    from hub_gateway.config import Settings
    from hub_gateway.contexts import get_context
    from hub_gateway.memory import CogneeClient, DatasetMap
    from hub_gateway.skills import SkillIndex
    from memory_admin import admin_client, read_env

    os.environ.update(HUB_CONTEXT=args.context, HUB_DEV_NO_AUTH="1", HUB_PUBLIC_URL="http://localhost:8000",
                      HUB_DATA_DIR=tempfile.mkdtemp(prefix="mnemos-eval-"))
    os.environ.setdefault("HUB_CONTEXTS_FILE", str(ROOT / "config" / "contexts.yaml"))
    ctx = get_context(args.context)
    admin = admin_client(args.url, read_env(ROOT / ".env"))
    corpus = load_jsonl(args.eval_dir / "corpus.jsonl")
    all_queries = load_jsonl(args.eval_dir / "queries.jsonl")
    queries = [q for q in all_queries if q["expect"]]

    if args.reuse:
        ds = json.loads(Path(args.reuse).read_text())
    else:
        stamp = int(time.time())
        ds = {n: admin.post("/api/v1/datasets", json={"name": f"zz_eval_{n}_{stamp}"}).json()["id"]
              for n in [*ctx.own_dataset_names, "shared"]}
    cog = CogneeClient(args.url, None, timeout=900)
    cog.headers = {"Authorization": admin.headers["Authorization"]}
    target = ds[ctx.own_dataset_names[0]]
    try:
        if not args.reuse:
            t0 = time.time()
            for i, n in enumerate(corpus, 1):
                await cog.remember(n["text"], target, node_set=n.get("tags"), run_in_background=False,
                                   metadata={"source_app": "eval", "tags": n.get("tags")})
                print(f"\rloading {i}/{len(corpus)}", end="", file=sys.stderr, flush=True)
            print(f"\rloaded {len(corpus)} notes in {time.time() - t0:.0f}s", file=sys.stderr)
        settings = Settings.from_env()
        server = build_server(settings, cognee=cog, datasets=DatasetMap(ds),
                              skills=SkillIndex(tempfile.mkdtemp(), args.context),
                              audit=Audit(args.context, os.path.join(settings.data_dir, "audit.log")))
        report: dict[str, Any] = {"context": args.context, "notes": len(corpus), "modes": {}}
        async with Client(server) as m:
            for mode in args.modes:
                ranks, lat, misses, chars = [], [], [], []
                for q in queries:
                    params: dict[str, Any] = {"query": q["q"], "top_k": 5, "include_shared": False}
                    if mode != "graph":
                        params["mode"] = mode
                    t = time.perf_counter()
                    out = _data(await m.call_tool("memory_search", params))
                    lat.append(time.perf_counter() - t)
                    texts = [x.get("text", "") for x in out["results"]]
                    chars.append(sum(len(t) for t in texts))
                    r = rank_of(texts, q["expect"])
                    ranks.append(r)
                    if r is None:
                        misses.append(q["q"])
                report["modes"][mode] = {**score(ranks, lat, chars), "misses": misses}
                print(f"{mode:10s} {report['modes'][mode]}", file=sys.stderr)
            if args.answer:
                report["answer"] = await eval_answers(m, all_queries)
                print(f"answer     {report['answer']}", file=sys.stderr)
        return report
    finally:
        if args.keep:
            Path(args.keep_file).write_text(json.dumps(ds))
            print(f"kept datasets: --reuse {args.keep_file}", file=sys.stderr)
        else:
            for i in ds.values():
                admin.delete(f"/api/v1/datasets/{i}")


def main() -> int:
    from memory_admin import BASE

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--context", default="personal", help="context whose tools are evaluated")
    ap.add_argument("--modes", nargs="+", default=["graph"], help="memory_search modes to compare")
    ap.add_argument("--eval-dir", type=Path, default=ROOT / "eval")
    ap.add_argument("--url", default=BASE)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--keep-file", default=os.path.join(tempfile.gettempdir(), "mnemos-eval-datasets.json"))
    ap.add_argument("--reuse")
    ap.add_argument("--answer", action="store_true", help="also evaluate memory_answer (needs HUB_ANSWER_MODEL)")
    ap.add_argument("--out", type=Path, help="write the JSON report here")
    args = ap.parse_args()
    report = asyncio.run(run(args))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
