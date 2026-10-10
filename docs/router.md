# hub-router: one connector for every context

Design and rationale: [design/single-connector.md](design/single-connector.md). The three per-context connectors
keep working; the router is an *extra* URL (`https://mnemos.<tailnet>.ts.net/mcp`).

## How sign-in works

1. The assistant (Claude, ChatGPT, Grok, Cursor) registers itself (DCR) and opens `/authorize`.
2. You sign in with one of the enabled methods:
   - **Local password** (default) — the same `HUB_LOCAL_USER` / `HUB_LOCAL_PASSWORD_HASH` as
     [auth-local.md](auth-local.md) (`python -m hub_gateway.local_auth hash`).
   - **Google** (optional) — see below.
   - **GitHub** (optional) — a *separate* GitHub OAuth app whose callback is the router's.
3. A consent page lists every context with an **unchecked** checkbox, plus an opt-in "switch between these
   contexts during a chat". At least one context is required.
4. The token gets scopes `user ctx:<a> ctx:<b> [switch]`. The decision is stored as a **grant** for that app;
   the dashboard lists grants and can revoke them, and revoking kills that app's tokens at once.

## Environment

| Variable | Meaning |
|---|---|
| `MNEMOS_ROUTER_PUBLIC_URL` | `https://mnemos.<tailnet>.ts.net` |
| `MNEMOS_ROUTER_STORAGE_KEY` | Fernet key for the encrypted grant/token store (`scripts/init_env.py`) |
| `MNEMOS_ROUTER_CONTEXTS` | optional subset, default: every context in `contexts.yaml` |
| `HUB_LOCAL_USER`, `HUB_LOCAL_PASSWORD_HASH` | local password sign-in (leave empty to disable it) |
| `MNEMOS_GOOGLE_CLIENT_ID`, `MNEMOS_GOOGLE_CLIENT_SECRET`, `MNEMOS_ROUTER_ALLOWED_EMAILS` | Google sign-in |
| `MNEMOS_ROUTER_GITHUB_CLIENT_ID`, `MNEMOS_ROUTER_GITHUB_CLIENT_SECRET`, `HUB_ALLOWED_GITHUB_LOGINS` | GitHub sign-in |
| `HUB_INTERNAL_KEY_<CTX>`, `MNEMOS_BACKEND_<CTX>` | how the router reaches each gateway's internal listener |

An allowlist is mandatory for Google and GitHub: without it the router refuses to start (any account would pass).

## Google sign-in (optional)

Nothing is created for you. In Google Cloud Console:

1. Create (or pick) a project → **APIs & Services → OAuth consent screen**: type *External*, publishing status
   *Testing*, add your own address under *Test users*. Scopes: `openid`, `email` only.
2. **Credentials → Create credentials → OAuth client ID → Web application**.
   Authorized redirect URI: `https://mnemos.<tailnet>.ts.net/oidc/google/callback`.
3. Put the client ID/secret in `.env` (`MNEMOS_GOOGLE_CLIENT_ID`, `MNEMOS_GOOGLE_CLIENT_SECRET`) and your address
   in `MNEMOS_ROUTER_ALLOWED_EMAILS`. Only verified emails on the list are accepted.

## GitHub sign-in (optional)

GitHub → Settings → Developer settings → OAuth Apps → New: homepage `https://mnemos.<tailnet>.ts.net`,
callback `https://mnemos.<tailnet>.ts.net/oidc/github/callback`. Fill `MNEMOS_ROUTER_GITHUB_CLIENT_ID/SECRET`.

## Testing

`scripts/oauth_probe.py <url> --user <u> --contexts personal,side [--switch]` runs the whole flow (DCR, PKCE,
sign-in, consent, token, MCP, refresh rotation) like a real client.

## Tools

The router lists the gateways' tools once, each with a required `context` argument (`hub_whoami` is answered by
the router and shows the contexts this app was granted). A call for a context the app was not granted fails with
a clear error; nothing is forwarded. Allowed calls go to that context's gateway over its internal listener
(`HUB_INTERNAL_KEY_<CTX>`, Docker network only) with `X-Mnemos-Login` / `X-Mnemos-App`, so memories saved
through the router show e.g. `Cursor via mnemos` as their origin. Secrets stay in the gateways.

## Enable it on your instance

Additive and reversible: the three `hub-<ctx>` connectors are not touched.

1. `python3 scripts/init_env.py` — adds `HUB_INTERNAL_KEY_<CTX>` and `MNEMOS_ROUTER_STORAGE_KEY` to `.env`
   without changing existing values (or `scripts/mnemos_context.py add` for new contexts).
2. Sign-in: `gateway/.venv/bin/python -m hub_gateway.local_auth hash` and put `HUB_LOCAL_USER=<you>` and
   `HUB_LOCAL_PASSWORD_HASH=<hash>` in `.env` (and/or Google/GitHub, above). On a hub that uses GitHub login these
   two variables are ignored by the gateways (`HUB_AUTH_PROVIDER` stays `github`).
3. `COMPOSE_PROFILES=router` in `.env`, then `scripts/update.sh` (or `docker compose up -d`). The gateways now also
   listen on `:8100` on the Docker network only.
4. `scripts/mnemos expose` lists `mnemos` next to the hubs; `--apply` starts what is missing. The watchdog
   checks `mnemos.<tailnet>.ts.net` too.
5. In the assistant, add the connector `https://mnemos.<tailnet>.ts.net/mcp`, sign in, tick the contexts.

Undo: remove `router` from `COMPOSE_PROFILES` and `docker compose rm -sf hub-router ts-mnemos` (then delete the
`mnemos` machine in the Tailscale admin if you want the name back).

## Switching contexts during a chat (opt-in)

Only for apps whose consent included "switch". They get `hub_use_context(context)`; afterwards calls may omit
`context` and use the active one for the rest of that MCP session. Never beyond the granted contexts; an explicit
`context` argument always wins. Apps granted a single context can omit `context` too. Without the switch scope
the tool is hidden and refused.

## Connected apps (dashboard)

The dashboard shows a **Connected apps** card when `COMPOSE_PROFILES` includes `router`: each app that went
through the consent page, the account it signed in with, its contexts and whether it may switch. **Revoke** deletes
the grant; that app's tokens stop working immediately and it has to sign in (and choose contexts) again.
The dashboard talks to the router's admin listener on `127.0.0.1:${MNEMOS_ROUTER_ADMIN_PORT:-8210}` with
`MNEMOS_ROUTER_ADMIN_KEY`; that listener is not on the tailnet or Funnel. Revoking works only from
`http://127.0.0.1` (same write guard as the other dashboard edits).
