#!/usr/bin/env python3
"""Capture style eval (0.13): what auto-capture would save from typical extractor output.

eval/persona/capture_style.jsonl holds fictional extracted facts with the language the person wrote in and a label
(good = worth remembering, as a note to self). "before" = the 0.12 gate; "after" = gate + memfilter.polish
(no third-person subject, vague items dropped, other-language facts sent to review instead of saved).

  python scripts/capture_eval.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway" / "src"))
from hub_gateway import memfilter as mf  # noqa: E402

THIRD = re.compile(r"^\s*(the (person|user)|user\b|el usuario|la persona|la usuaria)", re.I)


def score(kept: list[dict], rows: list[dict]) -> dict:
    good = sum(1 for r in rows if r["good"])
    tp = sum(1 for k in kept if k["good"])
    return {"saved": len(kept), "precision": round(tp / len(kept), 3) if kept else 0.0,
            "recall": round(tp / good, 3) if good else 0.0,
            "third_person": round(sum(1 for k in kept if THIRD.search(k["text"])) / len(kept), 3) if kept else 0.0,
            "wrong_language": round(sum(1 for k in kept if mf.language(k["text"]) not in ("", k["lang"]) and len(k["text"].split()) >= 4)
                                    / len(kept), 3) if kept else 0.0}


def main() -> None:
    rows = [json.loads(x) for x in Path(__file__).resolve().parents[1].joinpath("eval/persona/capture_style.jsonl").read_text().splitlines() if x.strip()]
    before, after, review = [], [], 0
    for r in rows:
        f = mf._norm_fact({k: r[k] for k in ("text", "kind", "importance", "durability")})
        g = mf.gate([f])
        if g:
            before.append({**g[0], "good": r["good"], "lang": r["lang"]})
            p = mf.polish(g, r["source"])
            review += bool(p and p[0].get("unsure"))
            if p and not p[0].get("unsure"):
                after.append({**p[0], "good": r["good"], "lang": r["lang"]})
    print(json.dumps({"items": len(rows), "before": score(before, rows), "after": {**score(after, rows), "to_review": review}}, indent=2))


if __name__ == "__main__":
    main()
