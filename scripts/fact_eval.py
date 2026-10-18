#!/usr/bin/env python3
"""Synthetic memory-quality eval (see gateway/src/hub_gateway/fact_eval.py).

  python scripts/fact_eval.py            # deterministic: write filter + rules extractor
  python scripts/fact_eval.py --v06      # same with the 0.6 filter (the baseline)
  python scripts/fact_eval.py --llm      # also extract from the persona chats with the local model (slow)
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway" / "src"))
from hub_gateway import fact_eval as fe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v06", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--home", default=None, help="Mnemos home for the engine (use a temp folder)")
    a = ap.parse_args()
    keep = fe.keep_v06 if a.v06 else None
    res = fe.run(keep=keep)
    if a.llm:
        res["llm"] = asyncio.run(fe.run_llm(Path(a.home) if a.home else None, keep or fe.pipeline))
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
