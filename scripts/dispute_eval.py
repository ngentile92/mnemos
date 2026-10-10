#!/usr/bin/env python3
"""Eval for memory_dispute ('This isn't right') against the local model. Fictional notes only (eval/dispute.jsonl).

  python scripts/dispute_eval.py [--url http://127.0.0.1:11434] [--model llama3.1:8b]
Reports per case: proposals, forbidden hits (must be 0), expected found, latency. Exit 1 if any forbidden hit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.dispute import propose  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    ap.add_argument("--model", default=os.environ.get("HUB_ANSWER_MODEL", "llama3.1:8b"))
    a = ap.parse_args()
    cases = [json.loads(line) for line in (ROOT / "eval" / "dispute.jsonl").read_text().splitlines() if line.strip()]
    bad = found = want = 0
    times = []
    for c in cases:
        notes = [{"id": k, "dataset": "eval", "text": v} for k, v in c["notes"].items()]
        t = time.time()
        props = await propose(a.url, a.model, c["correction"], notes, timeout=120)
        times.append(time.time() - t)
        ids = [p["id"] for p in props]
        hits = [i for i in ids if i in c["forbid"]]
        ok = [i for i in c["expect"] if i in ids]
        bad += len(hits)
        found += len(ok)
        want += len(c["expect"])
        print(f"{c['case']:22} {times[-1]:5.1f}s proposals={ids} forbidden={hits} expected={ok}/{c['expect']}")
        for p in props:
            print(f"    {p['action']}: evidence={p['evidence']!r} -> {p.get('proposed_text', '')[:140]!r}")
    print(f"\nforbidden hits: {bad} · expected recall: {found}/{want} · median latency {sorted(times)[len(times)//2]:.1f}s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
