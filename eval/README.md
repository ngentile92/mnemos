# Retrieval eval

`corpus.jsonl`: 20 short fictional notes (Spanish and English). `queries.jsonl`: questions with the strings a
correct answer must contain (`expect`) and the notes that hold them (`keys`); `expect: []` means the corpus has no
answer (used to check that answers say "I don't know"). Run with `scripts/memory_eval.py` (see its help). It only
uses throw-away `zz_eval_*` datasets. Results are in `results.md`.

## memory_dispute ("This isn't right")

`python scripts/dispute_eval.py` runs `eval/dispute.jsonl` (fictional notes; includes the real-world failure
"Hoy en día no estoy buscando cambiar de trabajo", which used to rewrite notes about home and hobbies) against
the local model. Must report 0 forbidden hits. llama3.1:8b, 2026-10-10: forbidden 0 · expected 4/4 · median 3.7 s.

## Memory quality (synthetic persona)

`eval/persona/` is a fictional person (Lucía, a data engineer in Rosario) with chats, the facts a small model
typically pulls out of them (each labelled durable / trivia / transient / sensitive / secret / wrong) and a gold list
of 18 things a good memory must know. `python scripts/fact_eval.py` reports durable-fact precision (DFP), durable
recall, leaks and gold recall; `--v06` shows the 0.6.0 baseline, `--llm --home <temp dir>` adds a run with the local
model. CI fails if a change goes below `eval/persona/baseline.json`. No real data is ever used.

### Tuning discipline (write filter)
`candidates.jsonl` and `dev.jsonl` are the only sets rules may be tuned on. `holdout.jsonl` (20) and `holdout2.jsonl` (60, a
different persona, frozen before the rules it measures) are reported together as `holdout`; `holdout_excl_wrong` leaves out
hallucinated facts ("wrong"), which no text rule can detect — catching those needs the extractor's quote/evidence check.

### Grounding (invented facts)
`grounding.jsonl` (dev, the check was built on it) and `grounding_holdout.jsonl` (held out) pair a user message with
extracted facts labelled ok / invented. `memfilter.grounded` drops a fact whose quote is not in the source or that names
a person, place, product or number the user never wrote. It cannot see invented facts with no such anchor
("has two cats", "prefers light mode"); the held-out number shows that limit.

### Model entailment check (0.9.1)
`memfilter.entailed` asks the local model (or Apple's on-device model) whether the user's words support each fact.
Measured with `scripts/entail_eval.py` on Qwen2.5 1.5B (smaller than the app's Qwen3 4B, so a lower bound):

| set | real facts kept | invented dropped |
|---|---|---|
| dev (prompt tuned here), rules only | 1.00 | 0.76 |
| dev, rules + model | 1.00 | 1.00 |
| held-out `entail_holdout.jsonl` (frozen first), rules only | 1.00 | 0.00 |
| held-out, rules + model | 0.75 | 1.00 |

Because the model also rejects some real facts, a "no" never deletes: the fact is marked `unsure` (live capture sends
it to the inbox instead of saving it; the import review lists it last).
