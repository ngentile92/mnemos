# Memory model on the Docker hub (0.10)

The layered memory model of the local app (entities, typed facts, validity, use; nightly tidy-up; profile; ranking)
also runs on the full hub, as a **sidecar SQLite per context** (`<data_dir>/model.sqlite`, next to the ledger).
Cognee stays the source of truth: the model never rewrites or deletes a Cognee note, so turning it off (or deleting
`model.sqlite`) leaves memory exactly as it was.

## Turn it on (per instance, your decision)
1. Dry run, per context (no writes):
   `docker compose exec gateway-<ctx> python -m hub_gateway.model_admin sync`
2. Link the notes: `... model_admin sync --apply` → prints a batch `mig-xxxxxxxx`.
   Undo exactly: `... model_admin undo-sync mig-xxxxxxxx`.
3. Tidy up (supersede, expire, decay, summaries, profile): `... model_admin consolidate`
   (optional summaries with a local model: `--ollama http://host.docker.internal:11434 --model qwen3:4b`).
   Undo a run: `... model_admin undo-job cons-xxxxxxxx`.
4. Set `HUB_MEMORY_MODEL=1` for the gateways in `.env` and restart them: search ranking and `memory_profile` then use
   the model. Without it the gateways behave as before.
5. Nightly: `sync --apply` then `consolidate` (Cognee's `/remember` returns no note id, so new notes are linked by
   the nightly sync rather than at save time). Example for the existing nightly agent: add both lines to
   `scripts/cognify_nightly.sh` after cognify.

Inspect: `model_admin profile`, `model_admin entities`, `model_admin digest` (the weekly "still true?" list).
