# Full setup (like the author's)

> Spanish version: [`FULL-SETUP.es.md`](FULL-SETUP.es.md).

This is the **whole** self-hosted Mnemos, the way the reference instance runs it: the Docker hub with Cognee and
Ollama, three contexts (`work` / `personal` / `side`), secrets in Infisical and Vaultwarden, Tailscale sidecars, the
**single connector** (`https://mnemos.<tailnet>.ts.net/mcp`) with the built-in login + passkeys (Google optional),
the dashboard, the watchdog with phone alerts, nightly cognify + LLM hygiene, backups and `scripts/update.sh`.

Just want memory on one Mac, without Docker? Use the [Desktop app](INSTALL-mac.md) instead.

This page is the **map**: it gives the order and the decisions, and links to the detailed steps in
[`SETUP.md`](SETUP.md) (numbered `SETUP §N` below), [`router.md`](router.md), [`auth-local.md`](auth-local.md),
[`google-login.md`](google-login.md), [`CLIENTS.md`](CLIENTS.md) and [`OPERATIONS.md`](OPERATIONS.md).
Expect 1.5–3 hours the first time, most of it in web consoles (Tailscale, Infisical, the AI apps).

Placeholders: `<tailnet>` = your tailnet name (the part before `.ts.net`), `<host>` = the machine's MagicDNS name,
`<you>` = the login name you pick for Mnemos.

## 1. What you get

```
Claude / ChatGPT / Grok / Cursor
        │  MCP over HTTPS + OAuth (built-in login, passkey, optional Google)
        ▼
ts-mnemos (Funnel) → hub-router ──internal Docker network──► gateway-work / gateway-personal / gateway-side
                                                                 │            │             │
                                                       Cognee (graph + vectors)   Infisical (secrets)   skills
Tailnet only (never public): Vaultwarden :443, Infisical :8443, dashboard :8444 (optional)
Host: Ollama (llama3.1:8b + paraphrase-multilingual), dashboard 127.0.0.1:8790, launchd/systemd jobs
```

The per-context URLs (`https://hub-<ctx>.<tailnet>.ts.net/mcp`) keep working next to the router; you can use
either or both.

## 2. Before you start

### Machine

- **Always-on host**: a Mac (reference) or a Linux box. It must stay awake while plugged in, or the hub, the night
  jobs and the backups stop (macOS: `sudo pmset -c sleep 0 disksleep 0`, see SETUP §14).
- **RAM**: 16 GB recommended. Docker needs 8 GB+ (Cognee ~2 GB, Infisical ~1.3 GB, the rest < 0.5 GB) and Ollama
  needs ~5–6 GB more while `llama3.1:8b` is loaded. With 8 GB total, use an OpenAI key instead of Ollama.
- **Disk**: ~15 GB free (Docker images ~5 GB, Ollama models ~5.5 GB, data and local backups grow from there).

### Accounts and apps

| What | Needed? | What it is for |
|---|---|---|
| Docker Desktop (macOS) / Docker Engine + Compose v2 (Linux) | yes | runs the whole stack |
| git, Python 3.11+ | yes | clone, bootstrap scripts, dashboard, watchdog, tests |
| Tailscale account + app on the host (and your phone) | yes | private network for the admin UIs; Funnel is the only public entry |
| Ollama (<https://ollama.com>) **or** an OpenAI API key | one of them | Cognee's graph extraction, `memory_answer`, LLM hygiene, local semantic search (Ollama) |
| Claude / ChatGPT / Grok / Cursor accounts | the ones you use | the assistants that share the memory (plan limits in §8) |
| ntfy app (iOS/Android) or a Slack/Discord webhook | optional | watchdog alerts on your phone |
| restic + a backup target (external disk, B2, S3, …) | recommended | encrypted nightly backups |
| rclone + a cloud remote (e.g. Google Drive) | optional | offsite copy of the restic repo |
| Google Cloud project (free, no billing) | optional | "Sign in with Google" on the router |
| GitHub account | optional | only for GitHub login (OAuth apps) or a private skills repo |
| `gh` CLI | optional | convenience |

Vaultwarden and Infisical run inside Docker: you create their admin accounts yourself, no external sign-up.
Infisical's free tier allows 5 machine identities (3 contexts use 3).

### Decisions (pick now; changing some later costs a migration)

| Decision | This guide (reference setup) | Alternative |
|---|---|---|
| Login for the assistants | **built-in** (`HUB_AUTH_PROVIDER=local`) + passkeys, Google optional | GitHub OAuth: one OAuth App per context (SETUP §6) |
| Connectors | **single connector** (router) | one connector per context |
| Extraction LLM | **Ollama** `llama3.1:8b` (free, local, slower) | OpenAI `gpt-4o-mini` (paid, better) |
| Embeddings (Cognee) | local `paraphrase-multilingual-MiniLM-L12-v2` (fastembed) | OpenAI; **fixed on day one** (changing = reindex) |
| Skills | local folder `./skills-local` (edit in the dashboard) | private GitHub repo + deploy key |
| Contexts | `work`, `personal`, `side` | your own (`scripts/mnemos context add`) |

With the built-in login you do **not** need any GitHub account or OAuth App. The `GITHUB_USER` and `GH_OAUTH_*` lines
stay `__COMPLETAR__` in `.env` and are ignored, as long as you set `HUB_ALLOWED_GITHUB_LOGINS=<you>` (§4).

## 3. Install the host apps

1. **Docker**: SETUP §2 (start at login, 8 GB+ RAM). Verify `docker info` and `docker compose version`.
2. **Tailscale**: SETUP §3: MagicDNS + HTTPS certificates on, paste `config/tailscale/policy-snippet.hujson` into
   *Access controls* (it covers every `hub-*` sidecar **and** the router's `mnemos` sidecar: all use
   `tag:hub-public`), create a reusable, pre-approved auth key tagged `tag:hub-public`.
3. **Ollama** (skip if you use OpenAI):

   ```bash
   brew install ollama          # or the app from ollama.com; Linux: curl -fsSL https://ollama.com/install.sh | sh
   ollama pull llama3.1:8b      # ~4.9 GB: Cognee extraction, memory_answer, LLM hygiene
   ollama pull paraphrase-multilingual   # ~0.6 GB: embeddings for the gateways' local semantic search
   ollama list                  # both must show up
   ```

   | Model | Used by | Setting |
   |---|---|---|
   | `llama3.1:8b` | Cognee graph extraction | `LLM_MODEL` in `cognee.env` (option B) |
   | `llama3.1:8b` | `memory_answer` (cited answers) | `HUB_ANSWER_MODEL` (+ `HUB_LLM_URL`) in `.env` |
   | `llama3.1:8b` | nightly hygiene with LLM | `MNEMOS_HYGIENE=llm` (+ `MNEMOS_HYGIENE_MODEL`) in `.env` |
   | `paraphrase-multilingual` | `memory_search` mode `auto`/`hybrid` | `HUB_EMBED_URL` + `HUB_EMBED_MODEL` in `.env` |

   Cognee's own embeddings are **not** an Ollama model: they run inside the Cognee container (fastembed).

   Containers must reach Ollama on the host:
   - macOS: `http://host.docker.internal:11434`. If `docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags`
     does not answer, Ollama only listens on loopback: `launchctl setenv OLLAMA_HOST 0.0.0.0`, restart Ollama, and
     block port 11434 from the LAN in the macOS firewall.
   - Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `cognee` service (if you use
     `compose.generated.yaml`, edit `compose.yaml`, which is its template). The gateways share their `ts-<ctx>`
     sidecar's network and cannot take `extra_hosts`: point `HUB_EMBED_URL` at the host's Docker bridge IP instead
     (`ip -4 addr show docker0`, usually `http://172.17.0.1:11434`). Make Ollama listen there
     (`OLLAMA_HOST=0.0.0.0` in its systemd unit) and firewall port 11434 from the LAN.
     Simpler on a server: run Ollama as a container on the compose network, with no port on the host
     ([ollama-linux.md](ollama-linux.md), which also covers AMD iGPUs and the context/output caps).

## 4. Bootstrap the configuration

```bash
git clone https://github.com/ngentile92/mnemos && cd mnemos
./scripts/bootstrap.sh                 # .env + cognee.env (chmod 600), config/*.yaml, generated keys
python3 -m venv .venv && .venv/bin/pip install -e "gateway[dev]"
.venv/bin/python -m hub_gateway.local_auth hash     # pick a 12+ char password; prints scrypt:...
```

Fill `.env` (SETUP §4 has the details). For this setup:

| Variable | Value |
|---|---|
| `TS_TAILNET`, `NOTEBOOK_TS_NAME`, `TS_AUTHKEY_PUBLIC` | from Tailscale (§3) |
| `HUB_AUTH_PROVIDER` | `local` |
| `HUB_LOCAL_USER` / `HUB_LOCAL_PASSWORD_HASH` | `<you>` / the `scrypt:...` line |
| `HUB_ALLOWED_GITHUB_LOGINS` | `<you>` (add the line; the hubs only accept the logins on this list, and it defaults to `GITHUB_USER`, which is still `__COMPLETAR__`) |
| `HUB_PASSKEYS` | `1` |
| `COMPOSE_PROFILES` | `router` |
| `HUB_EMBED_URL` / `HUB_EMBED_MODEL` | `http://host.docker.internal:11434` / `paraphrase-multilingual` |
| `HUB_ANSWER_MODEL` | `llama3.1:8b` (`HUB_LLM_URL` defaults to `HUB_EMBED_URL`) |
| `MNEMOS_HYGIENE` | `llm` |
| `RESTIC_REPOSITORY` / `RESTIC_PASSWORD` | your backup target / a long password (on paper too) |
| `MNEMOS_ALERT_NTFY_TOPIC` | a long random topic (§9) |

Then `cognee.env`: comment out option A and uncomment option B (Ollama) exactly as written there (SETUP §7:
`instructor`, endpoint **with** `/v1`, `OLLAMA_NUM_CTX=8192`). Leave the embedding lines as they are.

Finally `config/contexts.yaml`: your name (`owner`) and what belongs in each context. Run `./scripts/bootstrap.sh`
once more after setting `TS_TAILNET` / `NOTEBOOK_TS_NAME` (it builds `VW_DOMAIN` and `INFISICAL_SITE_URL`).
Ignore `GITHUB_USER` and `GH_OAUTH_*` in its "still missing" list.

## 5. Start the private layer

```bash
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
tailscale serve --bg --https=443  http://127.0.0.1:8081   # Vaultwarden, tailnet only
tailscale serve --bg --https=8443 http://127.0.0.1:8082   # Infisical, tailnet only
```

(Not `scripts/mnemos expose --apply` yet: it would also start the gateways, which need SETUP §10–11 first. Never
`tailscale funnel` on the host.)

Then SETUP §9 (Vaultwarden account, then `VW_SIGNUPS_ALLOWED=false`; save `.env` and `cognee.env` as a secure note),
SETUP §10 (`bootstrap_infisical.py --bootstrap`, then **delete the Instance Admin Identity**; add your API secrets and
list them in `config/secret-policy.yaml`) and SETUP §11 (`bootstrap_cognee.py`).

## 6. Turn on the public layer

```bash
docker compose up -d                   # gateways, hub-* sidecars, router + ts-mnemos (COMPOSE_PROFILES=router)
.venv/bin/python scripts/smoke_test.py --quick
scripts/mnemos expose --check          # every hub-<ctx> and mnemos: ok (public DNS + /mcp → 401)
```

From your phone on mobile data **without** Tailscale: `https://mnemos.<tailnet>.ts.net/mcp` answers 401 and
`https://<host>.<tailnet>.ts.net` (Vaultwarden) does not answer at all.

## 7. Single connector, passkeys and Google

- Router details and what the consent page does: [`router.md`](router.md).
- **Passkey**: open `https://mnemos.<tailnet>.ts.net/passkey/enroll` on each device you want one on (each host
  needs its own: the router and every `hub-<ctx>` you use directly) — [`auth-local.md`](auth-local.md#passkeys-optional).
- **Google** (optional): [`google-login.md`](google-login.md), then `scripts/update.sh`.

## 8. Connect the assistants

Add **one** connector `https://mnemos.<tailnet>.ts.net/mcp`, sign in, tick the contexts this app may use.
Steps per client: [`CLIENTS.md`](CLIENTS.md). Plan limits to know first:

- **Claude**: Free allows one custom connector (enough with the router); one per context needs Pro/Max.
- **ChatGPT**: developer mode, web only. Full read **and** write only on Business/Enterprise/Edu; **Pro is
  read-only** (`memory_save` blocked); Plus is not listed.
- **Grok**: grok.com/connectors → Custom. Business/Enterprise need an admin first.
- **Cursor**: `~/.cursor/mcp.json` with the URL, no secrets.

Check in each one: `hub_whoami`, save a fact in one client and find it from another. The dashboard's
**Connected apps** card lists each app and can revoke it.

## 9. Keep it running

```bash
./scripts/dashboard.sh install-agent            # http://127.0.0.1:8790 (scripts/mnemos expose --dashboard: tailnet :8444)
./scripts/install_watchdog_agent.sh --run-now   # every 15 min: Funnel/DNS + /mcp; recreates a missing sidecar
./scripts/install_cognify_agent.sh              # 03:47: cognify pending memories + hygiene (MNEMOS_HYGIENE)
./scripts/install_backup_agent.sh --run-now     # 03:17: restic backup (+ offsite copy if rclone is set up)
```

On Linux the same scripts install systemd user units (or run `scripts/install_systemd_units.sh`, then
`sudo loginctl enable-linger "$USER"`); see [SETUP → Linux](SETUP.md#linux-systemd).

- **Phone alerts (ntfy)**: install the ntfy app, *Subscribe to topic* → the same long random topic as
  `MNEMOS_ALERT_NTFY_TOPIC` (server `https://ntfy.sh` unless you set `MNEMOS_ALERT_NTFY_SERVER`). Then run
  `./scripts/hub_watchdog.py --test-alert`: the phone must buzz. Anyone who knows the topic can read it.
- **Hygiene**: `MNEMOS_HYGIENE=report|llm|apply|off` (default `report`). `llm` adds contradictions and outdated
  notes using `MNEMOS_HYGIENE_MODEL` (default `llama3.1:8b`). Reports go to `~/mnemos-hygiene/`; nothing is edited
  except exact duplicates with `apply`. Run once by hand: `scripts/mnemos hygiene --llm llama3.1:8b`.
- **Backups**: `brew install restic`, then `./scripts/backup.sh` and `./scripts/restore.sh --check` (SETUP §14).
- **Offsite copy** (optional): `brew install rclone`, `rclone config create gdrive drive scope=drive.file`
  (browser OAuth; rclone only sees what it creates), optionally `MNEMOS_OFFSITE_REMOTE=gdrive:mnemos-backups` in
  `.env`, then `scripts/offsite_sync.sh` once. `backup.sh` runs it at the end of every backup. Needs
  `RESTIC_REPOSITORY` to be a local folder.
- **Keep the host awake**: `sudo pmset -c sleep 0 disksleep 0` (macOS, on power).

## 10. Update and rollback

```bash
scripts/update.sh --dry-run     # what would change
scripts/update.sh               # latest v* tag; re-renders compose, rebuilds, smoke test, auto-rollback on failure
```

Never `docker compose down -v`: the volumes are your memory.

## 11. Final checklist

- [ ] `scripts/mnemos expose --check` → all ok; Vaultwarden/Infisical not reachable without Tailscale
- [ ] Every assistant: `hub_whoami` shows only the contexts you ticked; a fact saved in one shows up in another
- [ ] Passkey sign-in works on your phone
- [ ] Dashboard green; Connected apps lists your assistants
- [ ] `./scripts/hub_watchdog.py --test-alert` reaches your phone
- [ ] Next morning: `~/Library/Logs/mnemos-cognify.log` (Linux: `~/.local/state/mnemos/`) shows the night run and a
      hygiene report exists in `~/mnemos-hygiene/`
- [ ] `restic snapshots --tag mnemos` lists a snapshot; once a month, test a full restore elsewhere
