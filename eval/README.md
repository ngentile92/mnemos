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
