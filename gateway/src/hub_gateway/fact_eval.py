"""Synthetic memory-quality eval (no real data): a fictional persona in eval/persona/.

  candidates.jsonl    facts as a small model emits them, each labelled durable / trivia / transient / sensitive /
                      secret / wrong. Measures the write filter: durable-fact precision (DFP) of what is kept,
                      durable recall, and leaks (trivia, secrets, sensitive topics kept by default).
  conversations.jsonl chats (one per line, user messages only) of the same persona.
  gold.jsonl          "must know" facts with keyword groups; gold recall = share of gold facts found in kept facts.

`run()` is deterministic (rules + the write filter, no model) so CI tracks it on every PR; `scripts/fact_eval.py
--llm` adds a run with the local model. eval/persona/baseline.json holds the numbers a change must not go below.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3] / "eval" / "persona"
GOOD = {"durable"}


def load(name: str, root: Path = ROOT) -> list[dict]:
    return [json.loads(x) for x in (root / name).read_text().splitlines() if x.strip()]


def keep_v06(texts: list[str]) -> list[str]:
    """What 0.6 kept from a model's output: 10–300 chars and no secret."""
    from .local_discover import has_secret
    return [t for t in texts if 10 <= len(t.strip()) <= 300 and not has_secret(t)]


def pipeline(texts: list[str]) -> list[str]:
    """The current write filter (replaced as the filter improves)."""
    try:
        from .memfilter import keep_texts
    except ImportError:
        return keep_v06(texts)
    return keep_texts(texts)


def candidate_metrics(keep: Callable[[list[str]], list[str]], cands: list[dict]) -> dict[str, Any]:
    by = {c["text"]: c["label"] for c in cands}
    kept = keep([c["text"] for c in cands])
    lab = [by.get(t, "?") for t in kept]
    n_good = sum(1 for c in cands if c["label"] in GOOD)
    good_kept = sum(1 for x in lab if x in GOOD)
    return {"kept": len(kept), "of": len(cands),
            "dfp": round(good_kept / len(kept), 3) if kept else 0.0,
            "durable_recall": round(good_kept / n_good, 3) if n_good else 0.0,
            "trivia_kept": lab.count("trivia") + lab.count("wrong"),
            "secret_kept": lab.count("secret"), "sensitive_kept": lab.count("sensitive"),
            "transient_kept": lab.count("transient")}


def gold_recall(facts: list[str], gold: list[dict]) -> dict[str, Any]:
    low = [f.lower() for f in facts]
    hit = [g["id"] for g in gold if any(all(any(k in f for k in grp) for grp in g["any"]) for f in low)]
    return {"gold_recall": round(len(hit) / len(gold), 3) if gold else 0.0, "gold_hit": len(hit), "gold": len(gold)}


def rules_facts(convs: list[dict]) -> list[str]:
    from .local_discover import facts_from_messages
    return facts_from_messages([m for c in convs for m in c["messages"]])


def run(root: Path = ROOT, keep: Callable[[list[str]], list[str]] | None = None) -> dict[str, Any]:
    keep = keep or pipeline
    cands, convs, gold = load("candidates.jsonl", root), load("conversations.jsonl", root), load("gold.jsonl", root)
    out = {"filter": candidate_metrics(keep, cands)}
    rf = keep(rules_facts(convs))
    out["rules"] = {"facts": len(rf), **gold_recall(rf, gold)}
    out["filter_gold"] = gold_recall(keep([c["text"] for c in cands]), gold)
    return out


async def run_llm(home: Path | None, keep: Callable[[list[str]], list[str]]) -> dict[str, Any]:
    """Extract from the persona chats with the local model (one call per conversation), then filter."""
    import tempfile

    from . import local_discover as ld
    from . import local_engine
    home = home or Path(tempfile.mkdtemp(prefix="mnemos-eval-"))
    llm = local_engine.get(home).llm("import")
    if llm is None:
        return {"error": "no model available"}
    convs, gold = load("conversations.jsonl"), load("gold.jsonl")
    try:
        from .memfilter import extract_conversation
    except ImportError:
        extract_conversation = None
    raw: list[str] = []
    for c in convs:
        if extract_conversation:
            raw += [f["text"] for f in await extract_conversation(llm, c["messages"], app=c["app"], ts=c["ts"])]
        else:
            raw += await ld.batch_facts(llm, "".join(m + "\n---\n" for m in c["messages"]))
    kept = keep(raw)
    return {"engine": llm.name, "raw": len(raw), "kept": len(kept), **gold_recall(kept, gold), "facts": kept}
