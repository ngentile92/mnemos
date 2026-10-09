# Editing memory: what Cognee supports (Memory Editor, phase 0)

Verified against `cognee/cognee:1.6.1` (the image pinned in `compose.yaml`), by reading its
OpenAPI/source and by running probes in a **throw-away dataset** (created, tested, deleted).

## Findings

| Question | Answer |
|---|---|
| Can an existing note be edited in place? | **Yes**: `PATCH /api/v1/update?data_id=…&dataset_id=…` (multipart `data`, optional `node_set`). |
| Does the note keep its id? | **Yes**. `data_id` and `createdAt` are kept; `updatedAt` changes. |
| Is the graph re-extracted? | Yes. Without `node_set` the update is **`incremental`** (only changed chunks are replaced and re-extracted, ~10 s with a local 8B model). |
| What if tags (`node_set`) are sent? | It falls back to **`full_rebuild`** (`fallback.reason = unsupported_metadata`). |
| Is `external_metadata` (our provenance) kept? | **Incremental: yes. Full rebuild: no** — it is replaced by `{"_cognee": {...}, "node_set": [...]}`, so `source_app`, `saved_at`, `hub_context` are lost. |
| Does Cognee keep versions/history? | **No**. The old text is overwritten. |
| Does deleting a note clean the graph? | Yes, with reference counting: nodes shared with other notes survive. |
| Can a single node/edge be edited? | No HTTP API. Editing the graph directly is not recommended: it is derived from the notes and gets rebuilt. |
| Does `recall` say which note a result came from? | No: `GRAPH_COMPLETION` returns context text without note ids. |

## Consequences for Mnemos

1. **The note (text) is the source of truth; the graph is derived.** All edits go through the note.
2. Use `PATCH /api/v1/update` for corrections (keeps id and date) and send `node_set` **only when tags change**.
3. Because Cognee neither versions notes nor reliably keeps metadata, Mnemos keeps its **own small
   registry** (SQLite in the gateway data dir) with provenance (app, login, context, dates) and every
   prior version of each note. That registry is what makes history and undo possible.
4. Attributing a search result to a note stays a text search (`memory_list contains=`) for now.

## Reproducing the probe

With the stack running, as hub-admin (see `scripts/memory_admin.py` for the login): create a dataset,
`POST /api/v1/remember` with `run_in_background=false` and `external_metadata`, then
`PATCH /api/v1/update` with and without `node_set`, compare `GET /api/v1/datasets/{id}/data`, and
finally `DELETE /api/v1/datasets/{id}`. Never run it against a real dataset.

## Provenance registry

Every `memory_save` is recorded in `ledger.sqlite` inside the gateway data dir (`HUB_DATA_DIR`,
the `gw_<context>` volume, already included in `scripts/backup.sh`): source app (as declared by the MCP
client), login, context, saved/updated dates and tags. `memory_list` shows it under `provenance`.
When Cognee saves in the background it does not return the note id yet; the entry is bound to the
note by text hash the first time it is listed. Notes saved before the registry existed show only the
metadata Cognee kept. `memory_search` cannot show provenance: Cognee's graph answers carry no note ids.

## History and undo

- `memory_update` edits in place with `PATCH /api/v1/update` (same id and creation date). Tags are sent
  only when they change, so ordinary corrections stay incremental and keep Cognee's metadata.
- Before every correction or removal the current text is copied to the registry (`versions` table).
- `memory_history(id)` shows provenance and previous versions, newest first.
- `memory_undo(id)` reverts the last correction (repeat to keep going back) or restores a removed note
  (saved again, so it gets a new Cognee id; the old id still finds its history).
- All of it is limited to the context's own datasets; `shared` is still edited only as hub-admin.
