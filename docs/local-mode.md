# Single-process local mode (no Docker)

Phase 0 of the non-technical installation track. `mnemos-local` runs **every context in one Python process**:
no Docker, no Cognee, no Infisical, no Tailscale. It is what Mnemos Desktop (Tauri) will start, and what a
Claude Desktop `.mcpb` bundle runs over stdio. Your Docker setup (Mnemos Server) is untouched.

```bash
python3 -m venv .venv && .venv/bin/pip install -e gateway
.venv/bin/mnemos-local init --home ~/Mnemos --contexts personal,work
.venv/bin/mnemos-local serve --home ~/Mnemos            # http://127.0.0.1:8765/<context>/mcp
.venv/bin/mnemos-local stdio --home ~/Mnemos --context personal
```

| | Server (Docker) | Local mode |
|---|---|---|
| Notes | Cognee (graph + vectors) | SQLite, one file per dataset (`data/notes/`) — the note is the source of truth |
| Search | local index + Cognee graph | local index (keyword; semantic if Ollama is configured); `mode=graph` = keyword scan |
| Contexts | one container each | one process; isolation in code: a context's store only opens its own + shared + bridged dataset files |
| Auth | GitHub or built-in OAuth | HTTP: bearer token in `~/Mnemos/token` (0600), 127.0.0.1 only, Host checked; stdio: none |
| Secrets | Infisical | not wired yet (`secret_http_request` has no secrets) — Keychain is Phase 1 |
| Config | `.env` + `config/` | everything in `MNEMOS_HOME` (`contexts.yaml`, `skills/`, `data/`, `token`) |

Same tools, ledger (provenance, history, undo, pin/obsolete, validity) and dashboard logic in both modes.
Tested in `gateway/tests/test_local_mode.py` (end-to-end tools, cross-context isolation, HTTP token/Host guard).

## Auto-discover import

`hub_gateway/local_discover.py` backs **Import → Scan this Mac** (`GET /api/discover/scan`, `POST
/api/discover/preview`, `GET /api/discover/status`, `POST /api/discover/commit`, `POST /api/discover/undo`).
The scan is bounded (depth, file count, bytes, ~15 s) and read-only (SQLite opened `mode=ro&immutable=1`); the
client can only refer to source ids from the last scan, never paths. Preview drops secret-looking text and
near-duplicates of existing notes; commit tags each note `import-auto` and `imp-<batch>`, and undo deletes that
batch. Env: `MNEMOS_SCAN_HOME` (scan another home, used by tests), `MNEMOS_DISCOVER_LLM=0` (skip Ollama),
`HUB_OLLAMA_URL` / `HUB_ANSWER_MODEL` (which Ollama model extracts facts).

## Engines: Rules, Built-in, Ollama, API

`hub_gateway/local_engine.py` picks who does the smart parts (endpoints `GET /api/engine`, `POST
/api/engine/download|decline|remove|api|api/test`):

| Feature | Order |
|---|---|
| Import (facts from chats) | API key (if enabled for import) → Ollama → Built-in → Rules |
| Cleanup (contradictions; merges by meaning) | API key (if enabled for cleanup) → Ollama → Built-in → Rules |
| Search / duplicate check (embeddings) | Ollama (`HUB_EMBED_URL`/`HUB_EMBED_MODEL`) → Built-in → keywords |

Built-in models (downloaded into `<home>/models`, resumable with HTTP Range, sha256-checked, pinned revisions):
Qwen2.5-1.5B-Instruct Q4_K_M (1.12 GB, Apache-2.0) and EmbeddingGemma-300M Q8_0 (334 MB, Gemma Terms of Use), run
in-process by llama.cpp (`pip install "mnemos-hub[local-ai]"`; bundled in the Mac app, Metal on Apple Silicon).
For import the 1.5B model judges the sentences the rules propose (one short call each) instead of writing facts:
small models are reliable judges and unreliable writers. Ollama and API models write facts directly.

API keys: macOS Keychain via `/usr/bin/security` (key passed on stdin, service `io.mnemos.desktop.apikey`), or
`<home>/apikeys.json` (0600) off macOS / with `MNEMOS_NO_KEYCHAIN=1`; never in `engine.json`. Secrets are
stripped before any text reaches any model. Env: `MNEMOS_MODELS_MANIFEST` (replace the model list; CI uses a 1 MB
test GGUF), `MNEMOS_OLLAMA=0`, `MNEMOS_BUILTIN=0`.
