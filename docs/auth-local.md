# Built-in login (no GitHub OAuth app)

`HUB_AUTH_PROVIDER=local` turns each gateway into its own OAuth 2.1 authorization server with **one local user and
a password**. MCP clients (Claude, ChatGPT, Grok, Cursor, Codex) connect exactly as before; the only difference is
the page they open to log in. GitHub (`HUB_AUTH_PROVIDER=github`) stays the default.

## Setup

```bash
.venv/bin/python -m hub_gateway.local_auth hash      # asks the password twice (12+ chars), prints scrypt:...
```

In `.env`:

```bash
HUB_AUTH_PROVIDER=local
HUB_LOCAL_USER=alex
HUB_LOCAL_PASSWORD_HASH=scrypt:32768:8:1:...         # the line printed above (no $ signs, safe for compose)
```

`docker compose up -d` (gateways are recreated). The `GH_OAUTH_*` variables are not needed. All hubs share the same
user; the context isolation is unchanged (one connector per context, one URL per context).

## What the server does

| Endpoint | Behaviour |
|---|---|
| `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp` | Discovery (RFC 8414 / 9728) |
| `/register` | Dynamic Client Registration (RFC 7591); public clients (`token_endpoint_auth_method: none`) |
| `/authorize` | PKCE S256 required; stores the request (10 min, single use) and redirects to `/local-login` |
| `/local-login` | Login **and** consent: shows the client's name and the host it will return to. Not frameable (CSP + X-Frame-Options) |
| `/token` | `authorization_code` (code single-use, 5 min) and `refresh_token` (rotation: the old pair stops working) |
| `/revoke` | Token revocation |

- Access tokens: opaque, 1 h. Refresh tokens: 30 days. Storage keys are SHA-256 of the tokens; the store is
  Fernet-encrypted on disk (`/data/oauth-local`, same key as the GitHub provider's store).
- Password: scrypt (N=2^15, r=8, p=1), constant-time comparison. 5 failed logins in 10 min lock the login for 10 min.
- The token carries `login=<HUB_LOCAL_USER>`, so the existing owner check (`AuthMiddleware`) applies unchanged.
- No CIMD (Client ID Metadata Documents) in this mode: clients use DCR. In Claude pick *Register automatically*
  if it asks for the OAuth client type.

## Verified

`scripts/oauth_probe.py` plays the client: discovery → DCR → `/authorize` → login (wrong password first) → code →
token → MCP `initialize` + `tools/list` → refresh → old tokens rejected. It passes against a real gateway started on
a separate port (`HUB_PORT=18300`, scratch data dir) and in the unit tests (`gateway/tests/test_local_auth.py`).

```bash
MNEMOS_PROBE_PASSWORD=... .venv/bin/python scripts/oauth_probe.py https://hub-personal.<tailnet>.ts.net --user alex
```

Not verified yet with the real Claude / ChatGPT / Grok / Cursor UIs (they need a public instance in local mode).

## Not done (yet)

- **Passkeys (WebAuthn)**: feasible as a second factor or password replacement on `/local-login` (needs a small
  script on the page and the `webauthn` library); left out to keep the first version dependency-free.
- One user per instance. Several users would need per-user allowlists per context.

## Passkeys (optional)

Set `HUB_PASSKEYS=1` in `.env` and run `scripts/update.sh`. Applies to hubs with `HUB_AUTH_PROVIDER=local` and to
the router. The password keeps working; a passkey is an extra, faster way in.

1. Open `https://<hub-or-router-host>/passkey/enroll` in the browser where you want the passkey (Mac Safari/Chrome
   → iCloud Keychain / Google Password Manager; phone; security key).
2. Enter your user and password, optionally a name, and confirm with Touch ID / your device.
3. Next time an assistant connects, the sign-in page shows **Use a passkey** under the password form.

A passkey is tied to the host it was created on (`hub-side.<tailnet>.ts.net` and `mnemos.<tailnet>.ts.net` need one
each). Credentials (public keys only) are kept in the encrypted OAuth store of that hub. To remove all passkeys,
stop the hub and delete the `passkeys` collection in its OAuth store, or turn `HUB_PASSKEYS` off.
Hubs that use the GitHub provider are not affected.
