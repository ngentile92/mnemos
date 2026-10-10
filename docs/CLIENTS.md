# Connecting each AI client

One connector per context. URL pattern: `https://hub-<ctx>.<tailnet>.ts.net/mcp` (get the exact list with
`scripts/mnemos expose`). Every client logs in with **your** GitHub account (the only login in
`HUB_ALLOWED_GITHUB_LOGINS`); nothing secret is pasted into any client.

What every hub offers (checked against a live instance, 2026-10-10):

| Check | Result |
|---|---|
| `POST /mcp` without a token | `401` + `WWW-Authenticate` |
| `/.well-known/oauth-protected-resource/mcp` | resource + authorization server = the hub itself |
| `/.well-known/oauth-authorization-server` | `authorization_code` + `refresh_token`, PKCE `S256`, client auth `none` / `private_key_jwt` |
| Client registration | Dynamic Client Registration (`/register`) **and** Client ID Metadata Documents (CIMD) |

Status legend: ✅ verified on the reference instance · ⚠️ from the vendor's docs, not re-verified here · ❓ open question.

## Claude (claude.ai, Desktop, mobile)

Requirements: Free allows **one** custom connector; Pro/Max for one per context. Team/Enterprise: an Owner adds
it first in *Organization settings → Connectors*. ⚠️ (Claude Help Center, checked 2026-10-10)

1. claude.ai → **Customize → Connectors → + Add → Add custom connector**. ⚠️
2. Name `Mnemos · Personal`, URL `https://hub-personal.<tailnet>.ts.net/mcp` → **Continue**. ⚠️
3. Authentication: **Sign in when needed** (or *Sign in now*). OAuth client: **Use Claude's published identity
   (Recommended)** — the hub supports CIMD ✅; *Register automatically* (DCR) also works ✅ (server side).
4. Leave *Request headers* empty → **Add** → GitHub login → authorize.
5. Repeat per context. In each chat: **+ → Connectors** and switch on only that context's connector. ⚠️
6. Connectors sync to Desktop and mobile (connections come from Anthropic's cloud, so the hub must be public —
   it is, through Funnel ✅).
7. Editing a connector = remove and add again. ⚠️

Check: ask "call hub_whoami" → `context` is the right one.

## ChatGPT (web only)

⚠️ Plan matters (OpenAI Help Center, updated 2026-10-08): full MCP (read **and** write) is in beta for
**Business / Enterprise / Edu**; **Pro** can connect MCP servers in developer mode with **read/fetch only** (so
`memory_save` may be blocked); Plus is not listed. Not available in the mobile app.

1. Enable developer mode: personal/Pro → **Settings → Apps → Advanced settings → Developer mode**; Business →
   the same, admins only; Enterprise/Edu → an admin grants it first. ⚠️
2. **Settings → Apps → Create**: name `Mnemos · Personal`, MCP server URL, authentication **OAuth**. ⚠️
3. **Scan Tools** → complete the GitHub login → wait for the scan → **Create**. The app shows a *Dev* label. ⚠️
4. In a chat, pick the app from the tools menu (or mention it). Be explicit: "use Mnemos · Personal, tool
   memory_search". ⚠️
5. Refresh tokens: the hub advertises `refresh_token` ✅, so ChatGPT should not need a new login when the access
   token expires. ❓ Not yet observed over days on the reference instance.
6. Read tools carry `readOnlyHint` ✅ so ChatGPT does not ask for confirmation on them; writes ask.

## Grok (grok.com)

1. grok.com/connectors → **New Connector → Custom**. ✅ (used by the reference instance)
2. MCP server URL, then complete the GitHub login. ✅
3. Grok discovers the tools; they are used automatically when relevant. ✅
4. Keep the per-app instructions under ~1,300 characters: longer server instructions made the Grok connector drop
   after `initialize` (seen on the reference instance). ✅
5. Grok Business/Enterprise: a team admin provisions the connector in the cloud console first. ⚠️

## Cursor

`~/.cursor/mcp.json` (no secrets, Cursor does the OAuth): ✅ (in daily use on the reference instance)

```json
{
  "mcpServers": {
    "hub-personal": { "url": "https://hub-personal.<tailnet>.ts.net/mcp" },
    "hub-side":     { "url": "https://hub-side.<tailnet>.ts.net/mcp" }
  }
}
```

Cursor opens the browser for GitHub on first use; *Settings → MCP* shows each server green with its tools.
Never put API keys in `mcp.json`: credentials go through `secret_http_request`.

## Other MCP clients

Any client that speaks streamable HTTP + OAuth 2.1 (DCR or CIMD) works the same way: Codex CLI has connected to
the reference instance ✅.

## Single connector (optional): one URL for every context

With the router enabled ([router.md](router.md)) you can add **one** connector instead of one per context:
`https://mnemos.<tailnet>.ts.net/mcp`. Sign-in is the router's (local password by default, Google/GitHub
optional), then a consent page where you tick the contexts this app may use (all unchecked) and, if you want,
"switch between these contexts during a chat". The per-context connectors keep working side by side.

What was verified (2026-10-10, scripted client `scripts/oauth_probe.py --contexts … --switch --call …` against a
test router on the reference machine, routed to the **live** gateways over their internal listeners, read-only
calls): ✅ DCR, PKCE, sign-in, consent (unchecked boxes, at least one context required), scopes
`user ctx:<granted> [switch]`, refresh rotation, `hub_whoami`, `skills_list` routed to the granted context,
a call for a non-granted context refused, `hub_use_context` refused outside the grant.

| Client | How | Status |
|---|---|---|
| Cursor | `~/.cursor/mcp.json`: `"mnemos": { "url": "https://mnemos.<tailnet>.ts.net/mcp" }` | ⚠️ not tried with a real Cursor yet |
| Claude | Settings → Connectors → Add custom connector → the URL above | ⚠️ |
| ChatGPT | Settings → Apps → Create (developer mode), OAuth, the URL above | ⚠️ |
| Grok | Connectors → custom MCP, the URL above | ⚠️ |

Each client that connects shows up in the dashboard under **Connected apps**, where it can be revoked.
Model hint: every tool takes `context` (omit it when the app has one context, or after `hub_use_context`).

## Confirmation checklist (for what is marked ⚠️/❓)

- Single connector, per client (start with Cursor): add the URL, sign in, tick one context, run `hub_whoami`
  (must list only that context), then `memory_search` with that context; check the dashboard's Connected apps.

- Claude: add one connector, log in, run `hub_whoami`; open the same chat on mobile and run it again.
- ChatGPT: create the app with OAuth, run `memory_search`, then try `memory_save` (note whether your plan allows it);
  a few days later check it still works without logging in again (refresh token).
- Grok Business / ChatGPT Enterprise: only if you use those plans.
