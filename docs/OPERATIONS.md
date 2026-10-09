# Mnemos — operations

> Spanish version: [`OPERATIONS.es.md`](OPERATIONS.es.md).

Day-to-day reference: tools, local testing, graph, dashboard, nightly cognify, hygiene, watchdog, backups. The
initial step-by-step setup is in [`SETUP.md`](SETUP.md).

> The LaunchAgents (`install_*_agent.sh`, `dashboard.sh install-agent`) and the watchdog notifications are **macOS
> only and optional**. They use the `MNEMOS_LABEL_PREFIX` prefix (default `io.mnemos`, used below). On Linux the
> equivalent is a systemd timer or a cron line that calls the same script (see README, *Scheduling on Linux*).

## Tools exposed by the gateways

| Tool | What it does | Limit |
|---|---|---|
| `hub_whoami` | Context, visible datasets and projects | – |
| `memory_search` | Searches the context's memory (+ `shared` with `include_shared`) | Context datasets only; no LLM |
| `memory_search` `mode` | `auto` (default): local hybrid search in `search.sqlite` (BM25 + Ollama embeddings if `HUB_EMBED_URL`/`HUB_EMBED_MODEL`), and Cognee's graph only if nothing is found; `graph`, `keyword`, `semantic`, `hybrid` to force one | Per-gateway local index synced from Cognee (only pulls new notes, every ≤60 s); never mixes other contexts' datasets |
| `memory_save` | Saves a fact to the context's dataset or to `shared` | In `side`, `project` is required |
| `memory_answer` | Short answer written by a **local** model (Ollama, `HUB_ANSWER_MODEL`) only from the notes found, with citations (each note's id) and `known=false` + `unknown` when memory does not know | Disabled without a model; an answer without valid citations is marked unknown |
| `memory_entity` | Living page for a person/company/project in the context: every note naming it (`[[Name]]` or the exact name) in chronological order, with ids, + cited summary if `HUB_ANSWER_MODEL` is set. Without `name`, lists linked entities | Computed on request from the local index; saves nothing |
| `memory_list` | Lists notes with **id**, dataset, date, source app and text (filters `contains`, `project`) | Context datasets (+ `shared` read-only with `include_shared`) |
| `memory_update` | Fixes one of the context's own notes in place (Cognee `PATCH`: same id and date; the previous version goes to history) | **Own** datasets only; never `shared` |
| `memory_delete` | Removes an own note (and what the graph extracted only from it); keeps a copy for undo | **Own** datasets only; the id is checked against the context's listing first |
| `memory_history` / `memory_undo` | Show previous versions of a note / undo the last fix or restore a deleted note | **Own** datasets only; history in the gateway's `ledger.sqlite` |
| `memory_promote` | Copies an own note to `shared` (visible to every context) with its origin (dataset, id, context, app) in the metadata and the ledger (`provenance.promoted_from`) | Without `confirm=true` it only returns a preview; the original is untouched; removing it from shared is hub-admin only |
| `skills_list` / `skills_get` | Skills visible to the context (own folder + `shared` + `hub-share`) | No traversal or symlinks |
| `secrets_list` | Names and allowed hosts, **never values** | – |
| `secret_http_request` | Makes the HTTPS request injecting the secret; the response comes back redacted | Exact policy host, port 443, no private IPs, no redirects |

There are no tools to delete secrets or run commands. Deleting/fixing memory: only the context's own notes
(`memory_delete` / `memory_update`, annotated as destructive so the client asks for confirmation). **`shared` rule:**
no connector deletes or edits it (the `ctx-*` users only have read+write there, no delete); fix it as `hub-admin` on
the host with `scripts/memory_admin.py list shared` / `delete shared <id> --yes`. The audit log records tool, id and
dataset, never the text.

## Quickstart

**Production:** follow [`SETUP.md`](SETUP.md). Summary:

```bash
./scripts/bootstrap.sh                          # generates .env / cognee.env and the deploy key
# fill in TS_*, GH_OAUTH_* and the LLM in cognee.env
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
python3 scripts/bootstrap_infisical.py --bootstrap
python3 scripts/bootstrap_cognee.py
docker compose up -d
python3 scripts/smoke_test.py --quick
```

**Local testing without accounts or keys** (Docker, no Tailscale or OAuth, fake LLM):

```bash
docker compose -f compose.dev.yaml up -d --build
python3 scripts/bootstrap_cognee.py --url http://127.0.0.1:18000 --dev \
    --datasets-out dev/state/cognee-datasets.json --dev-keys-dir dev/state
docker compose -f compose.dev.yaml up -d --force-recreate gateway-dev-work gateway-dev-personal gateway-dev-side
python3 scripts/smoke_test.py --dev             # context isolation, skills, secrets
docker compose -f compose.dev.yaml down -v
```

Dev gateways: `http://127.0.0.1:18101/mcp` (work), `:18102` (personal), `:18103` (side). No-auth mode refuses to
start if `HUB_PUBLIC_URL` is not localhost.

**Unit tests:**

```bash
cd gateway && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest -q
```

## Updating (`scripts/update.sh`)

```bash
scripts/update.sh --dry-run        # what would change
scripts/update.sh                  # latest v* tag (or a ref: scripts/update.sh v0.2.0, scripts/update.sh origin/main)
```

fetch → checkout the ref → `scripts/render_compose.py` (if `.env` has `COMPOSE_FILE=compose.generated.yaml`) →
`pip install` of the gateway into `.venv` → `docker compose build` (only if `gateway/` or `skills-sync/` changed) →
`up -d` → `scripts/smoke_test.py --quick` (3 tries). If anything fails it goes back to the previous ref by itself
(rebuild + up + smoke) and exits 1. It never runs `down` nor touches volumes or gitignored config (`.env`,
`cognee.env`, `config/*.yaml`, `secrets/`, `data/`). History in `dev/state/updates.log`. Manual rollback:
`scripts/update.sh <previous-ref>`.

## Full graph view (you only, host only)

`scripts/visualize.py` logs into Cognee as `hub-admin` (via `127.0.0.1:8010`, nothing from the tailnet or the
gateways), builds the combined view of every dataset (`POST /api/v1/visualize/multi`, one colour per context) and
opens the HTML.

```bash
python3 scripts/visualize.py --setup            # once: read + superuser for hub-admin (ctx-* unchanged)
python3 scripts/visualize.py                    # everything → ~/mnemos-graphs/global-YYYY-MM-DD.html
python3 scripts/visualize.py --context side     # one context (work | personal | side | shared); --with-shared adds shared
```

The HTML is `chmod 600` (it contains your memory) and loads d3 from `d3js.org`. No new route is added: gateways
still cannot see other contexts. To undo the superuser: `python3 scripts/visualize.py --revoke-superuser`. If
`python3` is the system one (no `httpx`), the script re-runs itself with the repo's `.venv/bin/python3`.

## Live dashboard (you only, host only)

```bash
scripts/dashboard.sh open        # starts it (if needed) and opens http://127.0.0.1:8787
scripts/dashboard.sh status | stop | restart | logs
scripts/dashboard.sh install-agent     # start automatically at login (LaunchAgent, KeepAlive)
scripts/dashboard.sh uninstall-agent   # back to manual mode
scripts/dashboard.sh tailnet-on        # also on https://<mac>.<tailnet>.ts.net:8444 (tailnet only, never Funnel)
scripts/dashboard.sh tailnet-off
```

With the LaunchAgent (`io.mnemos.dashboard`) installed, launchd starts it at every login and restarts it if the
process dies (after ~10 s). The same commands still work: `start`/`restart`/`status` go through `launchctl`, and
`stop` unloads it (it does not come back until the next `start` or login). Always `--host 127.0.0.1`.

A page refreshed every 20 s with: architecture diagram with each piece green/yellow/red, container status, the three
public hubs (Funnel + `/mcp` requiring OAuth), secrets per context (count in Infisical with each gateway's *Viewer*
identity and what the policy allows injecting), Cognee datasets with items/nodes, Ollama, skills, last restic backup
and the latest commits. **Only names, states and counts**: the credentials it uses are read from `.env` on the
server side, never reach the browser, and every response is checked against the `.env` values. It listens only on
`127.0.0.1` (refuses `0.0.0.0`), validates the `Host` header and loads nothing external. Port:
`MNEMOS_DASHBOARD_PORT` (default 8787). Log: `~/Library/Logs/mnemos-dashboard.log`.

**Tailnet (optional):** `tailnet-on` runs `tailscale serve --bg --https=8444 http://127.0.0.1:8787` (443 is
Vaultwarden and 8443 Infisical; change it with `MNEMOS_DASHBOARD_TS_PORT`) and checks it stays *tailnet only*;
otherwise it removes it. The server still listens only on `127.0.0.1` and also accepts the `Host`
`<magicdns-name>:8444` (read from `tailscale status --json` at startup). **Note:** the Explore tab shows memory text
and secret names to any device on your tailnet (the `tag:hub-public` sidecars cannot reach it if the policy gives
them no outbound access).

### Explore tab (`#explorar/<context>`)

Selector at the top: one button per context + **All**. For the chosen context it shows:

- Interactive **memory graph** (vendored Cytoscape.js, no CDN): one colour per dataset, search, "concepts only"
  filter, and in gold the entities that appear in more than one context (or in more than one dataset inside the
  context), with a "shared only" filter. Click a node → its neighbours.
- **Saved memories** with date, source app (what the MCP client declares when connecting; older ones say
  "unknown") and whether they are already in the graph or pending.
- **Visible skills** for that context (shared + own, same rules as the gateway); click → content.
- **Secrets**: only the name, domains, methods and header allowed by the policy. Never values.

Isolation: each context is queried with **its own Cognee API key** (the gateway's, so it sees only what that gateway
can see: its datasets + `shared`). Only **All** logs in as `hub-admin`. Credentials stay on the server; responses go
through the same check against `.env` values.

## Empty graph / pending memories (`scripts/cognify_pending.py`)

Every `memory_save` does `add` (always works) and triggers `cognify` in the background (LLM extraction). With Ollama +
`llama3.1:8b` and Cognee's default framework (`litellm_native`), the request goes in schema-less "JSON mode"; the 8b
model sometimes omits fields or returns lists where strings go, validation fails three times and **the cognify of the
whole dataset fails** (0 nodes, ~15 min per attempt). Fix: `STRUCTURED_OUTPUT_FRAMEWORK=instructor` +
`LLM_ENDPOINT=…:11434/v1` + `OLLAMA_NUM_CTX=8192` in `cognee.env` (Ollama enforces the schema as a grammar).

To process what is pending (incremental, deletes nothing; uses each context's key):

```bash
python3 scripts/cognify_pending.py --dry-run   # which datasets have unprocessed items
python3 scripts/cognify_pending.py             # triggers cognify where needed and waits; prints nodes/edges
```

The dashboard shows "N pending" per dataset.

**Every night at 03:47** (30 min after the 03:17 backup, which stops Cognee) the `io.mnemos.cognify` LaunchAgent runs
it via `scripts/cognify_nightly.sh`: it waits until no `backup.sh` is running and Cognee answers, then runs
`cognify_pending.py` for every context. Install: `scripts/install_cognify_agent.sh [--run-now]`. Log:
`~/Library/Logs/mnemos-cognify.log`.

**Memory hygiene.** `scripts/memory_hygiene.py` reads every note of every dataset as hub-admin (read-only) and writes
`~/mnemos-hygiene/hygiene-YYYY-MM-DD.md` (+ `.json`, chmod 600, the last 30 are kept): exact duplicates, near
duplicates, superseded series (same PR/task), related notes, notes without tags/source app and secret-shaped text
(id only). It also consolidates ("dream", no LLM): groups by entity the notes linking the same `[[Name]]`, proposes
the merged text for each near duplicate in the same dataset (the newest + the sentences of the old one that are
missing) and lists possibly stale notes (`--stale-days`, default 180). `--llm llama3.1:8b` adds Ollama hints per pair
(advisory). `--apply` deletes **only** exact duplicates in the same dataset (keeps the one with more tags; uses the
owner user); everything else stays a proposal to decide by hand with `memory_update` / `memory_delete` (or
`memory_admin.py` for shared). `cognify_nightly.sh` runs it after cognify according to `MNEMOS_HYGIENE` = `report`
(default) | `llm` | `apply` | `off` (plist variable).

## Funnel/DNS watchdog (`scripts/hub_watchdog.py`)

Every **15 min** the `io.mnemos.watchdog` LaunchAgent resolves the three `hub-<ctx>.<tailnet>.ts.net` through public
DNS (DoH to Cloudflare/Google = 1.1.1.1 / 8.8.8.8) and POSTs to `/mcp` expecting **401**. If the public record
disappears (local MagicDNS would still resolve) or the connection fails, it recreates **only** that `ts-<ctx>` +
`gateway-<ctx>` (`docker compose up -d --force-recreate …`), with a **30 min cooldown** per hub. It also checks
Cognee on `127.0.0.1:8010` and restarts the dashboard LaunchAgent if `:8787` does not answer. It **never** runs
`tailscale funnel` nor publishes anything new.

```bash
./scripts/install_watchdog_agent.sh --run-now
tail -f ~/Library/Logs/mnemos-watchdog.log
cat ~/Library/Logs/mnemos-watchdog-status.json   # shown by the dashboard
./scripts/hub_watchdog.py --dry-run            # check without recreating
```

Uninstall: `./scripts/install_watchdog_agent.sh --uninstall`. State and cooldown:
`~/Library/Application Support/mnemos/watchdog-state.json`. The dashboard has a "Watchdog Funnel/DNS" card.

### Alerts off the Mac (optional)

Off by default. To enable them, add one or both to `.env`:

- `MNEMOS_ALERT_WEBHOOK_URL=`: Slack or Discord incoming webhook (POST JSON with `text` and `content`).
- `MNEMOS_ALERT_NTFY_TOPIC=`: an [ntfy](https://ntfy.sh) topic. Subscribe from the phone app. Use a long random
  topic, since anyone who knows it can read it.

Then test with `./scripts/hub_watchdog.py --test-alert`. No need to reinstall the LaunchAgent: it reads `.env` on
every run. You get 1 alert when a component goes down (`hub-<ctx>`, `cognee`, `dashboard`) and 1 when it recovers.
Flapping within `MNEMOS_ALERT_COOLDOWN_S` (default 30 min) is not repeated. Messages carry no hostnames, tailnet or
IPs. No alerts in `--dry-run`, with `--no-notify`, or when the Mac is offline.

## Security (the minimum to know)

- `.env`, `cognee.env`, `secrets/`, `data/` and `backups/` are never committed (they are in `.gitignore`).
- The only public pieces are the three `hub-<ctx>`, all behind OAuth with an allowlist (`HUB_ALLOWED_GITHUB_LOGINS`).
- Each gateway has its own Cognee API key and its own Infisical identity: even a buggy gateway gets 403 from Cognee
  and Infisical for another context's data. The tests verify this.
- Audit log at `/data/audit.log` (JSONL) of each gateway: tool, context and result; never contents or secrets.
- Backups: `scripts/backup.sh` (restic, daily 03:17 via `scripts/install_backup_agent.sh`) and `scripts/restore.sh --check`.
- Offsite: at the end, `backup.sh` runs `scripts/offsite_sync.sh` → `rclone sync` of the (already encrypted) restic
  repo to `MNEMOS_OFFSITE_REMOTE` (default `gdrive:mnemos-backups`; Google Drive with `drive.file` scope: rclone only
  sees what it creates) + `rclone check` (MD5). Log `~/Library/Logs/mnemos-offsite.log`. The OAuth token lives in
  `~/.config/rclone/rclone.conf` (outside the repo; keep a copy in Vaultwarden). Restore from Drive without the Mac:
  `restic -r rclone:gdrive:mnemos-backups snapshots`.

## Export / import memory as Markdown (gbrain-compatible)

`scripts/memory_markdown.py` (on the host, as hub-admin) turns memory into a folder of `.md` files in gbrain's page
format (frontmatter `type`, `title`, `date`, `tags` + a `mnemos:` block with id, dataset and source app) and loads it
back:

```bash
.venv/bin/python3 scripts/memory_markdown.py export ~/mnemos-export                 # every dataset
.venv/bin/python3 scripts/memory_markdown.py export ~/mnemos-export --dataset personal
.venv/bin/python3 scripts/memory_markdown.py import ~/gbrain-export --dataset personal   # shows the plan
.venv/bin/python3 scripts/memory_markdown.py import ~/gbrain-export --dataset personal --yes
```

- The exported folder can be imported into gbrain (`gbrain import <dir>`) and a gbrain export (`gbrain export --dir`)
  can be imported here. gbrain's timeline section is kept as text.
- Import goes to ONE dataset, skips texts that already exist and hidden files, and saves nothing without `--yes`.
- The export holds your memory in plain text: store it like any private backup.

## Setting names (`MNEMOS_*` and legacy `AIHUB_*`)

Instance settings are named `MNEMOS_*` (`MNEMOS_LABEL_PREFIX`, `MNEMOS_OFFSITE_REMOTE`, `MNEMOS_HYGIENE`,
`MNEMOS_DASHBOARD_PORT`, ...). The old `AIHUB_*` names are still read as a fallback; if both exist, `MNEMOS_*` wins.
Existing `.env` files and LaunchAgents keep working unchanged.
