# Eval results

Corpus: 20 notes (~1,900 characters in total), 18 answerable queries, context `personal`, local Ollama
`llama3.1:8b` for extraction, Cognee 1.6.1.

| Date | Mode | hit@1 | hit@5 | MRR | p50 ms | avg chars returned |
|---|---|---|---|---|---|---|
| 2026-10-09 | graph (Cognee `GRAPH_COMPLETION`, context only) | 1.00 | 1.00 | 1.00 | 102 | 1,885 |

Note: with a corpus this small the graph search returns almost the whole memory on every query (1,885 of ~1,900
characters), so it always "hits". `avg_chars` is the number to watch as memory grows.

### 2026-10-09 — local search (`memory_search` mode, PR "local search")

| Mode | hit@1 | hit@5 | MRR | p50 ms | avg chars |
|---|---|---|---|---|---|
| graph (before) | 1.00 | 1.00 | 1.00 | 113 | 1,885 |
| keyword (SQLite FTS5/BM25, no keys) | 0.94 | 1.00 | 0.97 | 1 | 222 |
| semantic (Ollama `paraphrase-multilingual`) | 0.89 | 1.00 | 0.94 | 82 | 441 |
| hybrid (keyword + semantic, RRF) | 1.00 | 1.00 | 1.00 | 84 | 439 |
| **auto** (new default: hybrid, graph only if nothing local) | 1.00 | 1.00 | 1.00 | 83 | 439 |
| auto without embeddings (keyword only) | 0.94 | 1.00 | 0.97 | 1 | 222 |

Same answers found while returning ~4x less text, with note ids, and without calling Cognee.

### 2026-10-09 — `memory_answer` (local `llama3.1:8b`, notes from `mode=hybrid`)

| Metric | Value |
|---|---|
| answer accuracy (18 answerable) | 0.94 |
| answered and cited the right note | 0.94 |
| said "unknown" on the 3 unanswerable | 1.00 (0 hallucinations) |
| p50 latency | 2.2 s |

The one miss ("¿Tomo café con azúcar?") is a strict-match miss: the model answers "no" without repeating "sin azúcar".

### 2026-10-09 — `memory_entity` (entity pages per context)

5 entities (`eval/entities.jsonl`), notes gathered by `[[link]]` or exact name: recall 1.00, precision 1.00, p50 2 ms
(without summary; the optional summary uses `memory_answer`'s local model, ~2 s).
