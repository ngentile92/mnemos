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

## Apps and Activity (0.6)

**Open my memory → Apps** lists every AI app that used the single connector (`data/apps.json`): its **default
space**, the spaces it **may use** (Shared is always readable), **Pause** (every call refused until resumed) and
**Remove access** (refused until *Allow again*; remote apps also lose their sign-in). New apps get the default
space chosen at the top. **Activity** shows one line per call, newest first: "Cursor saved “Uses pnpm” · work",
"ChatGPT searched “trip” → 3 notes", refusals in red (`data/activity.sqlite`, last 5,000 events, short summaries
only). Edits made in the Notes screen are yours and are not logged here (the ledger keeps their history). Older
per-space entries (`/<ctx>/mcp`, `stdio --context`) bypass the connector, so they are not listed or pausable:
reconnect those apps from *Connect apps*.

## Graph fixes (0.6)

- **Every note** a space can read (own + Shared + bridged), straight from the store instead of `memory_list`'s
  500, ranked **pinned → written by hand or by an AI app → imported/mined**, newest first (up to 1,500 per space).
  The model reads them in that order in the background, 600 per run.
- **"Me" collapsed**: the prompt calls the author "me"; "me", "the user" and your name become one node, hidden
  unless **show me** is ticked, so the graph is not a star around you.
- **Alias merge**: same letters ignoring spaces/punctuation, plus EmbeddingGemma similarity ≥ 0.90 between names
  of the same (or unknown) type.
- **Types** (person, organisation, project, tool, place, topic) and **colour by** kind, space or group; **All
  spaces** view where entities found in several spaces get a white ring.
- **Directed, labelled edges** (subject → relation → object; labels shown when the graph is small, on hover and
  for the selected node), **groups** by label propagation, and a **Top** list.

## Reach from anywhere (0.6, opt-in)

Cloud AI apps (Claude on the web and phone, ChatGPT, Grok) cannot start a program on your computer, so they need a
public HTTPS address. **Open my memory → Apps → Reach from anywhere** sets one up with
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel). It is **off by default** and does nothing until you
press *Turn on*.

- **Needs**: Tailscale installed and signed in, and a Mnemos password (≥ 10 characters, stored hashed in
  `auth.json`, 0600). The first time, Tailscale may ask the tailnet admin to allow Funnel and HTTPS once; the
  screen shows that link.
- **What it runs**: `tailscale funnel --bg --https=<port> http://127.0.0.1:<local port>` on the first Funnel port
  (443, 8443, 10000) that nothing else on this machine uses, remembered in `data/remote.json`. *Turn off* runs
  `tailscale funnel --https=<port> off` for that port only; other Serve/Funnel settings are never touched.
- **What is reachable**: requests whose `Host` is the public name go to a separate app with only the OAuth
  endpoints (discovery, dynamic client registration, PKCE S256, refresh-token rotation, lockout after 5 wrong
  passwords; the same code as the hub router) and `/mcp`. The status page, `/app`, `/api`, the per-space addresses
  and the local token are not reachable from outside. While it is off, that name is refused.
- **Signing in**: the app opens a Mnemos page; type your password and tick the spaces it may use (none are
  ticked). The app is then listed in *Apps* as "from anywhere", with the same default space / may use / Pause /
  Activity as local apps; it can never use a space you did not tick. **Remove access** also deletes its OAuth
  grant (encrypted store in `data/oauth-remote`, key in `data/remote.key`), so its tokens stop working at once;
  to use it again, connect it again from that app.
- **Your computer must be awake** and Mnemos running. While it is on, Mnemos can keep the Mac from idle-sleeping
  when plugged in (`caffeinate -is`, untick to allow sleep); closing the lid still sleeps.

Adding the address (`https://<name>.ts.net[:port]/mcp`, shown with a Copy button):

| App | Where |
| --- | --- |
| Claude (web, phone, desktop) | Settings → Connectors → Add custom connector → paste → Connect |
| ChatGPT | Settings → Apps & Connectors → Advanced → Developer mode → Create → paste, OAuth |
| Grok / others | "remote MCP server" with OAuth: paste the address |
