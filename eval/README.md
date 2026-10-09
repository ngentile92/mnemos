# Retrieval eval

`corpus.jsonl`: 20 short fictional notes (Spanish and English). `queries.jsonl`: questions with the strings a
correct answer must contain (`expect`) and the notes that hold them (`keys`); `expect: []` means the corpus has no
answer (used to check that answers say "I don't know"). Run with `scripts/memory_eval.py` (see its help). It only
uses throw-away `zz_eval_*` datasets. Results are in `results.md`.
