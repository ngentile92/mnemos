# Eval results

Corpus: 20 notes (~1,900 characters in total), 18 answerable queries, context `personal`, local Ollama
`llama3.1:8b` for extraction, Cognee 1.6.1.

| Date | Mode | hit@1 | hit@5 | MRR | p50 ms | avg chars returned |
|---|---|---|---|---|---|---|
| 2026-10-09 | graph (Cognee `GRAPH_COMPLETION`, context only) | 1.00 | 1.00 | 1.00 | 102 | 1,885 |

Note: with a corpus this small the graph search returns almost the whole memory on every query (1,885 of ~1,900
characters), so it always "hits". `avg_chars` is the number to watch as memory grows.
