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
