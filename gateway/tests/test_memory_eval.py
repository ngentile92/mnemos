"""Tests de scripts/memory_eval.py: métricas y archivos del eval."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("memory_eval", ROOT / "scripts" / "memory_eval.py")
me = importlib.util.module_from_spec(spec)
spec.loader.exec_module(me)


def test_rank_ignores_case_and_accents():
    assert me.rank_of(["nada", "La CTO es LAURA MENDEZ"], ["Laura Méndez"]) == 2
    assert me.rank_of(["x"], ["y"]) is None


def test_score():
    s = me.score([1, 3, None, 6], [0.1, 0.2, 0.3, 0.4])
    assert s["hit@1"] == 0.25 and s["hit@5"] == 0.5 and s["mrr"] == round((1 + 1 / 3 + 1 / 6) / 4, 3)


def test_eval_files_are_consistent():
    corpus = {n["key"] for n in me.load_jsonl(ROOT / "eval" / "corpus.jsonl")}
    qs = me.load_jsonl(ROOT / "eval" / "queries.jsonl")
    assert len(corpus) >= 20 and len(qs) >= 20
    for q in qs:
        assert set(q["keys"]) <= corpus and bool(q["expect"]) == bool(q["keys"])
    texts = {n["key"]: n["text"] for n in me.load_jsonl(ROOT / "eval" / "corpus.jsonl")}
    for q in qs:  # every expected string really is in one of its notes
        for k in q["keys"]:
            assert me.rank_of([texts[k]], q["expect"]) == 1, q
