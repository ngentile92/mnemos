# Single-process local mode (no Docker)

Phase 0 of the non-technical installation track. `mnemos-local` runs **every context in one Python process**:
no Docker, no Cognee, no Infisical, no Tailscale. It is what Mnemos Desktop (Tauri) will start, and what a
Claude Desktop `.mcpb` bundle runs over stdio. Your Docker setup (Mnemos Server) is untouched.

```bash
python3 -m venv .venv && .venv/bin/pip install -e gateway
.venv/bin/mnemos-local init --home ~/Mnemos --contexts personal,work
.venv/bin/mnemos-local serve --home ~/Mnemos            # http://127.0.0.1:8765/mcp (every space)
.venv/bin/mnemos-local stdio --home ~/Mnemos            # every space, for Claude Desktop / Cursor / Codex
.venv/bin/mnemos-local stdio --home ~/Mnemos --context personal   # one space only (older configs, .mcpb)
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

## One connector, a default space per app (0.6)

The UI calls contexts **spaces**. Instead of one connector per space, every AI app gets **one** Mnemos
(`/mcp` over HTTP, or `mnemos-local stdio` without `--context`; `gateway/src/hub_gateway/local_router.py`):

- every tool takes an optional `context` (the space). Without it, the call uses **this app's default space**;
  `hub_use_context` switches for the rest of the chat; `hub_whoami` tells the AI its spaces, what each is for and
  its default. The instructions tell it to save to the default and **ask before saving in another space**.
- apps are recognised by the name they declare on connect (`clientInfo`: Claude Desktop, Cursor, Claude Code,
  Codex, VS Code…) and listed in `data/apps.json` with their default space and the spaces they may use (all, by
  default). It is a label, not a secret: anything that can start Mnemos on this computer already owns the data.
- the call runs on that space's own server in-process, so isolation, history, undo and provenance ("saved by
  Cursor") are unchanged. `/<context>/mcp` and `stdio --context` still work for older configs.
- **Connect Claude Desktop** writes one `mnemos` entry and removes the older `mnemos-<context>` entries it had
  written (after a backup of the config).

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
| Import (facts from chats) | API key (if enabled for import) → Ollama → Built-in → Apple Intelligence → Rules |
| Cleanup (contradictions; merges by meaning) | API key (if enabled for cleanup) → Ollama → Built-in → Rules |
| Search / duplicate check (embeddings) | Ollama (`HUB_EMBED_URL`/`HUB_EMBED_MODEL`) → Built-in → keywords |

Built-in models (downloaded into `<home>/models`, resumable with HTTP Range, sha256-checked, pinned revisions):
Qwen3-4B-Instruct-2507 Q4_K_M (2.5 GB, Apache-2.0) — or Qwen3-1.7B Q4_K_M (1.1 GB) on Macs with 8 GB RAM or
less (`MNEMOS_LLM_SIZE=4b|1.7b` overrides) — and EmbeddingGemma-300M Q8_0 (334 MB, Gemma Terms of Use), run
in-process by llama.cpp (`pip install "mnemos-hub[local-ai]"`; bundled in the Mac app, Metal on Apple Silicon).
The model writes facts from batches of messages (extract mode; see `docs/benchmarks/local-architecture.md`).
Embeddings use EmbeddingGemma's task prompts (document / search query / sentence similarity); duplicates by
meaning are cosine ≥ 0.80 on similarity vectors.

Apple Intelligence (Mac app on macOS 26 with Apple Intelligence on): Apple's on-device model through a small Swift
helper bundled in the app (`desktop/fm-helper`, built in CI with the macOS 26 SDK). Nothing to download; used for
import only when no built-in/Ollama model is there (it found fewer useful facts than Qwen3 4B in the benchmark and
raised more false alarms as a cleanup judge). Settings → Apple Intelligence turns it off (`"apple": "off"`).

Upgrading from 0.4: if you had said yes to the built-in model, the new one downloads in the background at start;
the old Qwen2.5 1.5B keeps working until the new file is verified, then it is deleted.

API keys: macOS Keychain via `/usr/bin/security` (key passed on stdin, service `io.mnemos.desktop.apikey`), or
`<home>/apikeys.json` (0600) off macOS / with `MNEMOS_NO_KEYCHAIN=1`; never in `engine.json`. Secrets are
stripped before any text reaches any model. Env: `MNEMOS_MODELS_MANIFEST` (replace the model list; CI uses a 1 MB
test GGUF), `MNEMOS_OLLAMA=0`, `MNEMOS_BUILTIN=0`.

## Full history (0.5)

Import → **Full history** reads every chat of the sources picked in "Scan this Mac" with the import model, in the
background (`local_mine.py`). Messages are batched per file in a fixed order; each batch is identified by the hash of
its text and recorded in `<home>/mining.json` after it is read, so quitting and reopening the app resumes, and a
later run only reads new batches. Found facts wait in that list until you save or dismiss them; saving goes through
`memory_save`, so its duplicate check (words and meaning) still applies. Needs a model (Built-in, Ollama, Apple
Intelligence, or an API key enabled for import). Hooks: `GET /api/mine`, `POST /api/mine/{start,pause,resume,save,dismiss}`.


## Your own messages, searchable (0.5)

While Full history reads your chats it also keeps the messages themselves (after the secret filter) in
`<home>/data/<ctx>/evidence.sqlite`, indexed like notes (keyword FTS5 + embeddings, rank fusion). `memory_search`
in local mode adds up to 3 `from_sources` passages next to the saved notes (`include_sources=false` turns it off);
saved notes take precedence. Import → Full history → **Forget source messages** deletes that copy.

## Graph view (0.5)

When a local model is there (built-in or Ollama; never an API or Apple Intelligence), opening Graph starts a
background pass that reads the notes 12 at a time and stores (subject, relation, object) triples per note in
`<home>/data/<ctx>/graph.sqlite` (`local_graph.py`). Only new or edited notes are read again; deleted notes drop out.
The view shows those entities and their relations; without a model it keeps the name/tag graph. No Cognee involved.

## Spaces screen (0.6)

**Open my memory → Spaces**: per space a name (`label`), a colour and a one-line description (what goes there; the
AI uses it to route), Shared explained, and **read-only bridges as toggles** ("Work can read Personal"). Edits go
to `contexts.yaml` (validated by the same parser, backup in `data/contexts.yaml.bak`) and are reloaded in place:
the single connector and the app pick them up on the next call, including stdio processes started by AI apps
(they watch the file's mtime). The space key (`work`) never changes. The legacy per-space URLs (`/<ctx>/mcp`)
see bridge changes after a restart.
