# Spike: gateway without Docker + `.mcpb` for Claude Desktop (2026-10-11)

Goal (plan "instalación no técnica", step 1): run Mnemos with no Docker on a clean environment and package it
for Claude Desktop. Result: **works**, with the limits below. Measured, not estimated.

## What was tested

| Test | Where | Result |
|---|---|---|
| `pip install` of the gateway into a fresh venv | Mac (macOS 26.5.2, Apple silicon), `~/mnemos-cleantest`, nothing shared with the live instance | **Fails with the system Python (3.9.6)**: the package needs ≥ 3.11. A clean Mac has no usable Python → users must not need one. |
| Same with `uv` (standalone, installed into the temp dir) + Python 3.12 | Mac | installs in **4 s** (warm network), venv **92 MB** |
| `mnemos-local serve` (2 contexts) on port 8799 | Mac | `/health` 401 without token, 200 with it; listens on **127.0.0.1 only**; **RSS idle ≈ 91 MB** (vs ~2 GB for the Docker stack with Cognee) |
| `.mcpb` (uv type) unpacked and launched exactly like Claude Desktop does (`uv run --directory <bundle> src/server.py`, stdio) with an empty uv cache | Mac and Linux box | first start **7 s** (Mac) / 4 s (box) incl. dependency install; 19 tools; `memory_save` → `memory_search` finds it; `skills_list` OK; Mnemos folder created on first run |
| Bundle | — | `dist/mnemos.mcpb` is **1.6 KB** (manifest + pyproject + 10-line entry point); deps (~107 MB venv + ~115 MB uv cache) are installed by the host on first run |
| Isolation | CI (`tests/test_local_mode.py`) | other context cannot list, search or delete; store refuses foreign dataset files |

## Not tested (needs Nico)

- **Real install in Claude Desktop** (double-click `dist/mnemos.mcpb` → Install → set folder/context →
  ask Claude "remember that …", then "what do you remember about …"). The launch path was reproduced by hand;
  whether Claude Desktop's bundled uv runtime behaves identically is unverified.
- A brand-new macOS user account (true "clean Mac"); here Homebrew's Python 3.12 may have been reused by uv.
- Windows.

## Limits found

- No knowledge graph in local mode (notes + local index). Semantic search needs Ollama configured; without it,
  keyword search only.
- `secret_http_request` has no secret store in local mode yet (Keychain = Phase 1).
- `hub_whoami` reports login `dev-no-auth` in local/stdio mode (cosmetic).
- The bundle installs `mnemos-hub` from GitHub at a pinned commit: needs network on first run, and the repo
  must stay public (or switch to bundling a wheel).

## Decision input

Single-process local mode is light enough (≈ 90 MB RAM) to be the Desktop backend from day one, with the note
as source of truth. Cognee embedded can be added later as an optional derived index.

Build: `scripts/build_mcpb.sh [commit]` → `dist/mnemos.mcpb`.
