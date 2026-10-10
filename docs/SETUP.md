# Mnemos — step-by-step setup

> Spanish version: [`SETUP.es.md`](SETUP.es.md).

Everything that could be automated is already in the repo (compose, gateway, scripts, tests). What is left needs
your accounts, your machine or your judgement. Each step says **which exact value** to set and **how to verify** it.
It is written for macOS with Docker Desktop (the reference host); on Linux the steps are the same except for Docker
Desktop, `host.docker.internal` (add it with `extra_hosts: ["host.docker.internal:host-gateway"]`) and the
LaunchAgents (use cron or systemd).

Conventions: `<tailnet>` = your tailnet name (the part before `.ts.net`), `<mac>` = the host's MagicDNS name in
Tailscale, `<user>` = your GitHub login (`GITHUB_USER`). Requirements: Docker + Compose v2, Python 3.11+, git, `gh`
(optional), 8 GB of free RAM for Docker; Ollama if you want free local extraction.

---

## 0. Security baseline (5 min)

1. FileVault on: `fdesetup status` → must say `FileVault is On.`
2. GitHub with a passkey or 2FA (it is the login for every connector).

## 1. Your private skills repo (optional)

Skip this and step 5 if you keep skills in a local folder: `SKILLS_DIR=./skills-local` (the default in `.env.example`),
edited from the dashboard. See README → *Skills: local folder or GitHub*.


Skills live in a separate **private** repo (`<user>/mnemos-skills`) that `skills-sync` clones read-only. Start it
from this repo's examples:

```bash
mkdir mnemos-skills && cp -R mnemos/examples/skills/. mnemos-skills/ && cd mnemos-skills
# one folder per context of config/contexts.yaml inside skills/, plus shared/
git init && git add . && git commit -m "Initial skills"
gh repo create <user>/mnemos-skills --private --source . --remote origin --push
```

Format and visibility rules: [`examples/skills/README.md`](../examples/skills/README.md).

## 2. Docker Desktop

1. Open Docker Desktop. Settings → General → *Start Docker Desktop when you sign in*.
2. Settings → Resources → Memory: **8 GB or more** (measured: Cognee ~2 GB, Infisical ~1.3 GB, the rest < 0.5 GB).
3. Verify: `docker info` without errors and `docker compose version` (v2).

## 3. Tailscale

1. Install the macOS client from <https://tailscale.com/download> (Standalone variant recommended) and log in.
   Also install the phone app with the same account.
2. CLI: in the Standalone app, *Settings → CLI integration → Install Now* (puts `tailscale` in `/usr/local/bin`).
   In the App Store variant the binary is `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
3. Admin console → **DNS**: MagicDNS **on** and **HTTPS Certificates → Enable**. Note the tailnet name
   (`<tailnet>.ts.net`) → `TS_TAILNET=<tailnet>` in `.env`. Note the Mac's name → `NOTEBOOK_TS_NAME=<mac>`.
4. Admin console → **Access controls**: paste/merge `config/tailscale/policy-snippet.hujson`
   (`tag:hub-public` owned by `autogroup:admin`, `nodeAttrs` with `funnel` only for `tag:hub-public`, and no rule
   with `src: tag:hub-public`).
5. Admin console → **Settings → Keys → Generate auth key**: *Reusable* ✔, *Ephemeral* ✘, *Pre-approved* ✔,
   *Tags* = `tag:hub-public`. Copy it to `TS_AUTHKEY_PUBLIC=` in `.env`.
6. Verify: `tailscale status` shows the Mac and the phone.

## 4. Prepare `.env` and `cognee.env`

```bash
cd mnemos
./scripts/bootstrap.sh          # generates keys, .env and cognee.env (chmod 600), config/*.yaml and the skills deploy key
python3 -m venv .venv && . .venv/bin/activate && pip install -e "gateway[dev]"   # for scripts and tests
```

`bootstrap.sh` lists what is still missing (at least `GITHUB_USER`, `TS_*` and `GH_OAUTH_*`). It also creates
`config/contexts.yaml` and `config/secret-policy.yaml` from the examples (gitignored): edit the `owner` and the
description of each context. Run it again after setting `TS_TAILNET` and `NOTEBOOK_TS_NAME`: it builds
`VW_DOMAIN=https://<mac>.<tailnet>.ts.net` and `INFISICAL_SITE_URL=https://<mac>.<tailnet>.ts.net:8443`.

## 5. Skills repo deploy key

`bootstrap.sh` prints `secrets/skills_deploy_key.pub`. At
`https://github.com/<user>/mnemos-skills/settings/keys` → *Add deploy key*: title `mnemos skills-sync`, paste the
key, **Allow write access: NO**.

## 6. Three GitHub OAuth Apps (one per context)

At <https://github.com/settings/developers> → *OAuth Apps* → *New OAuth App* (personal account, not an org):

| Application name | Homepage URL | Authorization callback URL | `.env` variables |
|---|---|---|---|
| `mnemos-work` | `https://hub-work.<tailnet>.ts.net` | `https://hub-work.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_WORK_ID` / `_SECRET` |
| `mnemos-personal` | `https://hub-personal.<tailnet>.ts.net` | `https://hub-personal.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_PERSONAL_ID` / `_SECRET` |
| `mnemos-side` | `https://hub-side.<tailnet>.ts.net` | `https://hub-side.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_SIDE_ID` / `_SECRET` |

*Enable Device Flow*: no. After creating each one: *Generate a new client secret* and copy it (shown only once).
Optional, to test login before exposing anything: a fourth app `mnemos-dev` with callback
`http://localhost:8000/auth/callback`.

## 7. Choose Cognee's LLM (in `cognee.env`)

Reading memory does **not** use an LLM (the gateway asks for `only_context`). Writing does (graph extraction).

- **Option A — API key** (best quality): keep `LLM_PROVIDER=openai`, `LLM_MODEL=openai/gpt-4o-mini` (or your
  preferred model) and set `LLM_API_KEY=`. Cost scales with what you save.
- **Option B — Ollama on the Mac** (free, slower): `ollama pull llama3.1:8b` (~4.9 GB) and in `cognee.env` comment
  out A and uncomment B with `LLM_MODEL=llama3.1:8b` (Cognee validates Llama 3.1/3.2 for extraction; with
  `qwen2.5:7b` it logs a *known limitations* warning and may lose entities), `STRUCTURED_OUTPUT_FRAMEWORK=instructor`,
  `LLM_ENDPOINT=http://host.docker.internal:11434/v1` (**with** `/v1`) and `OLLAMA_NUM_CTX=8192`. Without
  `instructor`, Cognee asks for schema-less "JSON mode": the 8b model sometimes returns missing fields, validation
  fails and the cognify of the whole dataset ends with 0 nodes (see README, *Empty graph*). Check the container can
  reach it: `docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags`. If it does not answer,
  Ollama listens on loopback only: `launchctl setenv OLLAMA_HOST 0.0.0.0` and restart Ollama (this exposes it to your
  LAN; better restrict it with the macOS firewall).
- Embeddings: **local and multilingual** by default (`fastembed`, `paraphrase-multilingual-MiniLM-L12-v2`). Do not
  change them after loading data (forces a reindex). To change the LLM later: edit only `LLM_MODEL` in `cognee.env`
  and `docker compose up -d cognee` (what was extracted stays; embeddings do not change).

## 8. Start credentials and memory (nothing public yet)

```bash
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
docker compose ps        # everything "running"; Infisical takes ~1 min the first time (migrations)
tailscale serve --bg --https=443  http://127.0.0.1:8081   # Vaultwarden (tailnet only)
tailscale serve --bg --https=8443 http://127.0.0.1:8082   # Infisical  (tailnet only)
tailscale serve status
```

**Never** use `tailscale funnel` on the Mac: the only public pieces are the `hub-*` sidecars.

Shortcut: `scripts/mnemos expose --apply` does these two `tailscale serve` lines (and checks the hubs later); it is idempotent.

## 9. Vaultwarden account

1. From a tailnet device open `https://<mac>.<tailnet>.ts.net` → *Create account* (long master password, written on paper).
2. In `.env`: `VW_SIGNUPS_ALLOWED=false` and `docker compose up -d vaultwarden`. Check *Create account* no longer works.
3. Save a **secure note** with the full contents of `.env` and `cognee.env` (master copy).

## 10. Infisical: admin account, projects and identities

```bash
python3 scripts/bootstrap_infisical.py --bootstrap
```

It asks for an email and password: **that is your Infisical admin account**. The script creates the `mnemos`
organization, the projects `hub-work`, `hub-personal`, `hub-side`, `hub-shared` (dev/staging/prod environments), the
folders `/shop`, `/blog`, `/mnemos` in `hub-side/prod`, and the identities `mi-gw-work|personal|side` (Universal
Auth, **Viewer** role on their project and on `hub-shared`), and writes `INF_MI_*` to `.env`. Then open
`https://<mac>.<tailnet>.ts.net:8443`, add your secrets in `prod` and list them (no values) in
`config/secret-policy.yaml`. Free tier limit: 5 identities.

**Delete the *Instance Admin Identity* when the bootstrap is done.** Nothing in the hub uses it (gateways and
dashboard use `mi-gw-*` with `INF_MI_*`; neither `.env` nor compose hold admin credentials). The bootstrap creates a
Token Auth token for it with a 90-day TTL that only lived in the script's memory. In the UI (with your admin user):
**Organization Settings → Access Control → Identities → Instance Admin Identity → ⋮ → Delete** (if it has *delete
protection*, turn it off on the same page first). Deleting the identity revokes its token. To import secrets later
(`import_env_secrets.py --apply`, which needs write access) create a temporary identity with the Member role on the
target projects and delete it afterwards, or add them by hand in the UI.

## 11. Cognee: users, datasets and API keys

```bash
python3 scripts/bootstrap_cognee.py
```

Reads `config/contexts.yaml` and creates `hub-admin`, one `ctx-<context>` user per context, one dataset per context
(or per project: with the example, `work`, `personal`, `side_shop`, `side_blog`, `side_mnemos`) and `shared`
(read+write for every context), writes `config/cognee-datasets.json` (gitignored) and the `COGNEE_KEY_*` in `.env`.
Idempotent.

## 12. Turn on the public layer and test

```bash
docker compose up -d                       # adds the 3 Funnel sidecars and the 3 gateways
python3 scripts/smoke_test.py --quick      # Cognee OK + 401 and OAuth metadata on the 3 hostnames
```

`scripts/mnemos expose` then shows every hub as `ok` (public DNS + `/mcp` → 401).

From mobile data **without** Tailscale: `https://hub-personal.<tailnet>.ts.net/mcp` answers 401;
`https://<mac>.<tailnet>.ts.net` (Vaultwarden) does **not** answer. The Tailscale admin shows `hub-work`,
`hub-personal`, `hub-side` tagged `tag:hub-public`.

## 13. Connect the assistants (one connector per context)

Detailed, up-to-date steps with what is verified: [`CLIENTS.md`](CLIENTS.md).

URLs: `https://hub-work.<tailnet>.ts.net/mcp`, `https://hub-personal.<tailnet>.ts.net/mcp`,
`https://hub-side.<tailnet>.ts.net/mcp`. In all of them you log in with **your** GitHub; any other account is rejected.

- **Claude** (web; then it shows up in Desktop and mobile): *Customize → Connectors → Add custom connector*. Name
  `Hub · Personal` (and the other two), URL above, OAuth auth (let it register the client automatically). Three
  custom connectors need a Pro or Max plan. In each chat enable only the one for that context.
- **ChatGPT** (web only): *Settings → Security and login → Developer mode* on. On chatgpt.com, create one developer
  mode app per context with the URL, streamable HTTP transport and **OAuth** auth. Try `memory_save`: depending on
  your plan it may be read-only.
- **Grok**: grok.com/connectors → *New Connector* → *Custom* → URL and authentication. If OAuth does not complete,
  try a local MCP bridge (e.g. MCP SuperAssistant).
- **Cursor**: in `~/.cursor/mcp.json` → `{"mcpServers": {"hub-side": {"url": "https://hub-side.<tailnet>.ts.net/mcp"}}}`
  (no secrets; Cursor does the OAuth).

Check on each one: `hub_whoami` returns the right context; save a fact in `personal` from Claude and search for it
from another client; from the `work` connector it does not show up.

## 14. Backups

```bash
brew install restic
# in .env: RESTIC_REPOSITORY (external disk or cloud) and RESTIC_PASSWORD (also in Vaultwarden and on paper)
./scripts/backup.sh
./scripts/restore.sh --check
```

Schedule it daily with the repo's LaunchAgent (macOS; on Linux: `17 3 * * * cd /path/to/mnemos && ./scripts/backup.sh`
in cron). It runs `backup.sh` every day at **03:17** local time; if the Mac was asleep, launchd runs it as soon as it
wakes:

```bash
./scripts/install_backup_agent.sh --run-now   # installs ~/Library/LaunchAgents/io.mnemos.backup.plist and runs once now
tail -f ~/Library/Logs/mnemos-backup.log        # log of each run
restic snapshots --tag mnemos                  # with RESTIC_REPOSITORY/RESTIC_PASSWORD exported from .env
```

Retention is applied by `backup.sh` itself at the end: `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`.
Uninstall: `launchctl bootout gui/$(id -u)/io.mnemos.backup && rm ~/Library/LaunchAgents/io.mnemos.backup.plist`.

Optional extra LaunchAgents (macOS only; prefix `MNEMOS_LABEL_PREFIX`, default `io.mnemos`):

```bash
./scripts/install_cognify_agent.sh      # daily 03:47: processes pending memories into the graph (log ~/Library/Logs/mnemos-cognify.log)
./scripts/dashboard.sh install-agent    # dashboard on 127.0.0.1:8787 at login; launchd restarts it if it dies
./scripts/dashboard.sh tailnet-on       # optional: also on https://<mac>.<tailnet>.ts.net:8444 (tailnet only, never Funnel)
./scripts/install_watchdog_agent.sh --run-now  # every 15 min: public Funnel DNS + /mcp; recreates a missing sidecar (log ~/Library/Logs/mnemos-watchdog.log)
```

Cognee is published on the host at `127.0.0.1:8010` (not 8000) so it does not clash with local dev servers that
usually take 8000. The gateways are unaffected (they use `cognee:8000` on the internal network).

Uninstall: `launchctl bootout gui/$(id -u)/io.mnemos.cognify && rm ~/Library/LaunchAgents/io.mnemos.cognify.plist`,
`./scripts/dashboard.sh uninstall-agent`, and `./scripts/install_watchdog_agent.sh --uninstall`.

To keep the Mac from sleeping while plugged in (so the hub and the nightly backup keep running), once:

```bash
sudo pmset -c sleep 0 disksleep 0   # the display still turns off on its own (displaysleep untouched)
pmset -g custom                     # under "AC Power": sleep 0, disksleep 0
```

Note: if `RESTIC_REPOSITORY` points to a folder on the same laptop (e.g. `~/mnemos-backups`), that covers mistakes
and a broken container, but not losing the laptop. Copy that folder to an external disk or the cloud (or point the
repo to an external target) — the offsite sync (`scripts/offsite_sync.sh`, see [OPERATIONS](OPERATIONS.md)) does this.

Once a month, test a full restore into another folder or machine.
