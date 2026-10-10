# Design: one MCP connector for every context

Status: **accepted** (2026-10-10), being implemented in steps (see the end).

## Goal and constraint

Today each context has its own connector (`hub-cloudburst`, `hub-personal`, `hub-side`): one URL, one GitHub OAuth app,
one gateway process, one Tailscale sidecar. It is safe, but each app needs three connectors and someone has to turn
on the right one in each chat.

Goal: **one** URL (`https://mnemos.<tailnet>.ts.net/mcp`) that works with every context.

Hard constraint: **the isolation stays real**. "Real" means it is enforced by credentials the model cannot change,
not by instructions or by a parameter the model fills in:

- each context's memory is reachable only with that context's Cognee API key (Cognee checks the dataset ACL);
- each context's secrets only through that context's Infisical machine identity and `secret-policy.yaml` section;
- skills by owner folder, bridges only where `contexts.yaml` declares them.

A single connector must not turn any of these into "the gateway promises not to".

## Decision (summary)

1. **The contexts are chosen when you log in**, on the consent page, per app (per OAuth client). The token that
   comes out carries exactly those contexts as scopes (`ctx:personal ctx:side`). Nothing in a chat can widen it;
   to change it you log in again.
2. **A front router, not one big gateway.** A new `hub-router` service is the only public piece of the new URL. It
   runs the OAuth server, checks the token, and forwards each tool call to the existing `gateway-<ctx>` of the
   context the call is for, over the internal Docker network, with an internal per-context credential. The
   per-context gateways keep their own Cognee key, Infisical identity and data directory, unchanged.
3. **Same tool names**, plus a `context` argument whose allowed values are only the contexts in the token. With
   one context in the grant the argument is optional, so the experience is the same as today's connectors.
4. **In-chat switching is a weaker, opt-in mode** (`hub_use_context`), off by default, chosen per app on the
   consent page, and always bounded by the granted contexts.
5. **The three current connectors keep working** untouched; the single connector is added next to them.

## What the MCP spec gives us (authorization, 2025-11-25)

- The MCP server is an OAuth 2.1 **resource server**; it publishes Protected Resource Metadata (RFC 9728) with
  `authorization_servers` and `scopes_supported`. The 401 `WWW-Authenticate` header MAY carry `scope="..."`.
- **Scope selection by clients**: use the `scope` from the 401 challenge; otherwise request **all**
  `scopes_supported`. The spec says the authorization server and the user then decide at consent. That is exactly
  what we need: the client asks for everything, the consent page narrows it, the token carries the narrowed set.
- **Resource indicators (RFC 8707)** are mandatory for clients: `resource=https://mnemos.<tailnet>.ts.net/mcp` in the
  authorize and token requests; the server MUST check the token audience. One resource per connector; we do not need
  "multiple resources" in one token (and clients would not ask for it).
- **Step-up**: on `403 insufficient_scope` with `scope="..."` clients SHOULD re-authorize with more scopes. Useful
  later for "add a context to this app", but client support is uneven, so the design must not depend on it.
- Token passthrough is forbidden: the router must not forward the client's token to the gateways (it does not:
  it uses its own internal credential per context).

## How the clients behave (what matters here)

| Client | One server, scopes | Relevant facts |
|---|---|---|
| Claude (web/Desktop/mobile) | Requests the scopes it discovers; consent page is ours | Connector auth settings cannot be edited after adding (remove/add again). Per-chat toggle is per connector, not per context. |
| ChatGPT (developer mode) | OAuth with DCR or CIMD; needs `refresh_token` | **Freezes a snapshot of the tool list** when the app is created/approved: the tool names and schemas must be stable; a `context` enum that changes per token could break calls → keep the schema generic (string) and validate server-side. |
| Grok | Custom connector, OAuth | Connector dropped after `initialize` when server instructions were long (> ~1,300 chars): instructions must stay short even with several contexts. |
| Cursor | `mcp.json` URL, OAuth in browser | Re-login is easy (Settings → MCP → reconnect); good test client. |
| Codex CLI | OAuth | Same as Cursor. |

Not verified with any client: step-up (`insufficient_scope`), and how each renders a consent page with checkboxes
(it is our HTML page, so it should just work). These are the first things to test in step 3.

## Token and scope model

Scopes (advertised in `scopes_supported` and in the 401 challenge):

| Scope | Meaning |
|---|---|
| `user` | Baseline (same as today) |
| `ctx:<name>` | Access to that context (memory, skills, secrets, exactly as its own connector) |
| `switch` | Allow `hub_use_context` (in-chat switching among the granted contexts) |

Flow:

1. The client asks for `user ctx:cloudburst ctx:personal ctx:side switch` (all of `scopes_supported`).
2. The consent page (GitHub login first, or the built-in login of `auth-local.md`) shows **checkboxes per context**,
   all **unchecked** by default, the app's name and redirect host, and a separate checkbox "allow switching in chat".
   Optional preset per client name ("ChatGPT → personal") stored in the router.
3. The router issues a token whose `scope` is the checked set (OAuth allows granting less than requested; the token
   response says which). Access token 1 h, refresh 30 days with rotation; refresh keeps the same scopes, never more.
4. Every request: token valid, audience = the router's resource, `login` in the allowlist, and the call's context
   ∈ the token's `ctx:*` scopes. Otherwise a tool error (not a silent fallback).

Grants live in the router's encrypted store, keyed by (login, client_id). A small "Connected apps" page (dashboard,
loopback only) lists them and revokes them; revoking deletes the refresh tokens.

## Routing: single gateway vs front router

| | A. One gateway with every context loaded | **B. Front router + existing gateways** |
|---|---|---|
| Credentials in one process | all Cognee keys, all Infisical identities | **none** (router has only internal routing secrets) |
| A bug in context selection | can read/write another context directly | can only reach a gateway the token allows; the gateway still only has its own keys |
| Code change | large (settings, scope objects, caches, ledger per context) | small: a proxy + an internal listener on each gateway |
| Per-context data dirs (ledger, local index, audit) | must be split by hand | unchanged |
| Latency | lowest | +1 local hop (Docker network, ~ms) |
| Existing connectors | must keep a second code path | **untouched** |

**Chosen: B.** The router is a FastMCP server whose tools mirror the gateways' tools. For each call it:

1. reads `context` (or the session's current context in switch mode, or the only granted one);
2. checks it against the token's scopes;
3. calls `gateway-<ctx>` on an **internal-only listener** (`:8100`, not behind the sidecar's Funnel) with an
   `X-Mnemos-Internal` header carrying `HUB_INTERNAL_KEY_<CTX>` and the caller's `login` and app name (for the audit
   and ledger provenance);
4. returns the gateway's result, adding `context` to it.

The gateway's internal listener accepts only that header (constant-time compare, one key per context, never the
client's token), runs the same tools with the same scoping, and is reachable only on the Docker network (the
gateway shares the sidecar's network namespace, so the listener binds to the container's Docker-network address,
and the sidecar's serve config publishes only :8000).

## Tool naming

- **Same names as today** (`memory_search`, `memory_save`, `skills_get`, `secret_http_request`...). Each gets a
  `context` string argument. Description: "one of the contexts you were granted; call `hub_whoami` to see them".
- `hub_whoami` lists the granted contexts, their datasets/projects and whether switching is allowed.
- No per-context tool copies (`personal_memory_search`...): the tool list would grow with every context and
  ChatGPT's frozen snapshot would go stale whenever a context is added.
- **Writes and secrets require an explicit `context`** when the grant has more than one context, even in switch mode.
  Reads may use the current context.
- No tool touches two contexts in one call. Bridges stay as they are (read-only, declared in `contexts.yaml`),
  served by the reader context's gateway.
- Server instructions: generated from the grant, kept under the 1,300-character limit (test), naming the contexts
  and "never copy data between contexts unless the user asks".

## In-chat switching (weaker mode)

`hub_use_context(name)` sets the current context of the MCP session, only among the granted ones, only if the token
has `switch`. It is weaker because the model, possibly steered by injected text (a web page, a memory note, a
skill), decides which context is used next. Mitigations: off by default; writes/secrets still need an explicit
`context`; every switch is audited and shown in results ("context: side"); a session never holds more than the
token's contexts. Isolation between the granted contexts is then only as strong as the model's behaviour, and that
must be said on the consent page.

## Secrets isolation

- The router never sees secret values or Infisical credentials; `secret_http_request` runs in `gateway-<ctx>` with
  that context's identity and policy, as today.
- `secrets_list` returns only the requested context's names.
- The internal keys (`HUB_INTERNAL_KEY_<CTX>`) are generated by `init_env`/`mnemos_context.py add`, live in `.env`,
  are redacted by the dashboard, and are rotated by recreating the pair.

## Infrastructure

- New services: `hub-router` + `ts-mnemos` sidecar (Funnel on `mnemos.<tailnet>.ts.net` only). `scripts/mnemos expose`
  learns about it; the watchdog checks it like the others.
- Auth: the router uses the same providers as today (GitHub OAuth app — one new app for the `hub` hostname — or the
  built-in login), plus the consent checkboxes. The current per-context gateways keep their own OAuth unchanged.
- `render_compose.py` adds the internal listener and key to every gateway.

## Migration (the 3 current connectors keep working)

1. Ship the router behind a flag (`MNEMOS_SINGLE_CONNECTOR=1`), nothing else changes.
2. Add the new connector in one client (Cursor), grant one context, compare with the old connector.
3. Then grant several contexts; then try ChatGPT/Claude/Grok.
4. Keep the per-context connectors as long as you want; they are not deprecated by this design. Removing one is
   only removing its connector in the app (its gateway is still needed behind the router).

## Risks

| Risk | Mitigation |
|---|---|
| Model picks the wrong context (writes to `side` what belonged to `personal`) | Explicit `context` on writes; results always name the context; `memory_history`/`memory_undo`; grant fewer contexts per app |
| Prompt injection steers the context in switch mode | Off by default; writes/secrets explicit; audit; consent text |
| Router bug forwards to a context not granted | Scope check in one function with tests; gateways log the caller app/login; property test "never forwards outside the grant" |
| Internal listener reachable from outside | Bind on the Docker network only; sidecar serve config unchanged; `mnemos expose` checks that :8100 is not published; test |
| ChatGPT frozen tool snapshot | Stable tool names and schemas; `context` as a plain string validated server-side |
| Client ignores granted scopes and keeps asking for all | Fine: the grant is decided by the consent page, not by the request |
| Bigger blast radius of one stolen token | Per-app grants with few contexts; short access tokens; revoke page |
| Long instructions break Grok | Generated, tested length limit |

## Implementation steps

1. Internal listener on each gateway (`:8100`, `X-Mnemos-Internal`, per-context key) + tests; not exposed. **Done.**
2. `hub-router` skeleton: OAuth (local, Google OIDC, GitHub) with `ctx:*` scopes and consent checkboxes, encrypted grant store. **Done.**
3. Router tool proxy with `context` argument, scope check, provenance headers; tests + `oauth_probe.py` with scopes. **Done.**
4. Compose/render: `hub-router` + `ts-mnemos`, internal keys in `init_env`/`mnemos_context.py`, `mnemos expose`, watchdog. **Done.**
5. In-chat switching (`switch` scope, `hub_use_context`), off by default. **Done.**
6. "Connected apps" page in the dashboard (list/revoke grants).
7. Client trials (Cursor → Claude → ChatGPT → Grok) and docs (`CLIENTS.md`, consent screenshots).

## Decisions taken (2026-10-10)

1. Context checkboxes on the consent page start **unchecked**; no per-app presets.
2. In-chat switching **is available**, opt-in per app on the consent page (`switch` scope), never beyond the granted
   contexts.
3. Router login: the **built-in local login** by default (`HUB_AUTH_PROVIDER=local`, same user/password hash as
   `auth-local.md`), plus **optional Google sign-in (OIDC)** configured by env (needs a Google Cloud OAuth client,
   see the router docs); **GitHub** stays an option.
4. The three per-context connectors **stay** until the router has proven itself.
5. URL: `https://mnemos.<tailnet>.ts.net/mcp` (sidecar `ts-mnemos`).
