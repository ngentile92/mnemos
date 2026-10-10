# Retrieval eval

`corpus.jsonl`: 20 short fictional notes (Spanish and English). `queries.jsonl`: questions with the strings a
correct answer must contain (`expect`) and the notes that hold them (`keys`); `expect: []` means the corpus has no
answer (used to check that answers say "I don't know"). Run with `scripts/memory_eval.py` (see its help). It only
uses throw-away `zz_eval_*` datasets. Results are in `results.md`.

## memory_dispute ("This isn't right")

`python scripts/dispute_eval.py` runs `eval/dispute.jsonl` (fictional notes; includes the real-world failure
"Hoy en día no estoy buscando cambiar de trabajo", which used to rewrite notes about home and hobbies) against
the local model. Must report 0 forbidden hits. llama3.1:8b, 2026-10-10: forbidden 0 · expected 4/4 · median 3.7 s.
