# AGENTS.md — instructions for AI agents working on or installing Mnemos

Mnemos is a self-hosted MCP server that gives several AI assistants (Claude, ChatGPT, Grok, Cursor…)
one shared long-term memory, skills and credentials, **isolated per context** (e.g. work / personal /
side projects). One gateway per context; memory is [Cognee](https://github.com/topoteretes/cognee);
secrets live in Infisical; public access only through Tailscale Funnel + GitHub OAuth.

## Ground rules

- Never commit `.env`, `cognee.env`, `config/*.yaml|json` (non-`.example`), `secrets/`, `data/`, `backups/`.
  They are gitignored; keep it that way. Nothing specific to one person's setup goes into the repo:
  make it configurable (`config/contexts.yaml`, env vars) and ship an `.example`.
- Never run `docker compose down -v` or delete volumes on a real instance: they hold the memory.
- Never print secret values. Scripts read them from `.env`; do the same.
- Ask the human before anything public or paid: Tailscale auth keys, GitHub OAuth apps, OpenAI keys.

## Installing for a human (macOS or Linux)

Follow `README.md` → *Quickstart*; the long version is `docs/SETUP.md`. In order:

1. Check requirements: Docker + Compose v2 (8 GB RAM for Docker), Python 3.11+, git, a Tailscale account
   (MagicDNS, HTTPS, Funnel), a private GitHub repo for skills, and either Ollama (`ollama pull llama3.1:8b`)
   or an OpenAI key.
2. `./scripts/bootstrap.sh` then `python3 -m venv .venv && .venv/bin/pip install -e "gateway[dev]"`.
3. Fill the `__COMPLETAR__` placeholders in `.env` (ask the human for each value; they come from
   Tailscale and GitHub), choose the LLM in `cognee.env`, describe contexts in `config/contexts.yaml`.
4. Add `secrets/skills_deploy_key.pub` as a read-only deploy key on the skills repo (human step).
5. `docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync`
6. `.venv/bin/python scripts/bootstrap_infisical.py --bootstrap` and `.venv/bin/python scripts/bootstrap_cognee.py`
7. `docker compose up -d` and `.venv/bin/python scripts/smoke_test.py --quick` — must end in `TODO OK` / all OK.
8. Connect each assistant to `https://hub-<context>.<tailnet>.ts.net/mcp` (README → *Connecting your assistants*).
9. Optional: backups (`scripts/install_backup_agent.sh`), watchdog (`scripts/install_watchdog_agent.sh`).

To try without any account: README → *Try it locally* (`compose.dev.yaml`, fake LLM, no OAuth).
Later updates: `scripts/update.sh [ref]` (rolls back automatically if the smoke test fails).

## Working on the code

- Gateway: `gateway/src/hub_gateway/` (FastMCP). `app.py` = tools, `memory.py` = Cognee client and
  per-context scoping, `ledger.py` = provenance/history registry, `secrets.py` = secret broker.
- Tests: `.venv/bin/pytest -q gateway/tests` (no network; Cognee is mocked). CI also runs shellcheck,
  `docker compose config` and gitleaks over the full history.
- Keep MCP server instructions (`instructions.py`) under 1,300 characters.
- A context must never read or write another context's datasets; add a test when touching scoping.
- Memory editing semantics (what Cognee supports): `docs/memory-editing.md`.
