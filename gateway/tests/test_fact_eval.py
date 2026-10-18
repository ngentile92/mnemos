"""Synthetic persona eval runs in CI: the write filter may not regress below eval/persona/baseline.json."""
import json

from hub_gateway import fact_eval as fe

BASE = json.loads((fe.ROOT / "baseline.json").read_text())


def test_persona_files_are_consistent():
    labels = {c["label"] for c in fe.load("candidates.jsonl")}
    assert labels <= {"durable", "trivia", "transient", "sensitive", "secret", "wrong"}
    assert len(fe.load("gold.jsonl")) >= 15 and len(fe.load("conversations.jsonl")) >= 10


def test_v06_baseline_is_what_baseline_json_says():
    m = fe.candidate_metrics(fe.keep_v06, fe.load("candidates.jsonl"))
    assert m["dfp"] == BASE["filter"]["dfp"] and m["trivia_kept"] == BASE["filter"]["trivia_kept"]


def test_no_regression_vs_baseline():
    r = fe.run()
    f, b = r["filter"], BASE["filter"]
    assert f["dfp"] >= b["dfp"]
    assert f["durable_recall"] >= b["durable_recall"] - 0.05
    assert f["secret_kept"] == 0
    assert f["trivia_kept"] <= b["trivia_kept"] and f["sensitive_kept"] <= b["sensitive_kept"]
    assert r["rules"]["gold_recall"] >= BASE["rules"]["gold_recall"] - 0.06


def test_gold_recall_matching():
    gold = [{"id": "x", "any": [["norte"], ["data engineer"]]}]
    assert fe.gold_recall(["Data engineer at Norte"], gold)["gold_hit"] == 1
    assert fe.gold_recall(["Works at Norte"], gold)["gold_hit"] == 0
