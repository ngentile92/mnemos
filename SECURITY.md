# Security

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
(*Security → Report a vulnerability*) on this repository. You should get an answer within a week.

## Threat model

Mnemos holds the most sensitive things you have: your memory across every AI assistant and credentials for your APIs.
The design assumes the **models and their providers are not fully trusted**, the **internet is hostile**, and the
**host machine and your GitHub account are trusted**.

| Asset | Threat | Mitigation |
|---|---|---|
| API secrets | Leaked to a model, a prompt injection or a log | Models only see names. `secret_http_request` injects the value server-side, only to the exact hosts in `config/secret-policy.yaml`, port 443, no redirects, private/loopback/CGNAT IPs blocked after DNS resolution, request headers/body cannot carry the secret, and the response is redacted. Infisical identities are *Viewer* only. The audit log never stores values. |
| Memory | Read or written by someone else over the internet | Only the Funnel hostnames are public, each behind GitHub OAuth (dynamic client registration) with an allow-list of logins. The dev no-auth mode refuses to start unless the public URL is localhost. |
| Context isolation | A model (or a gateway bug) reaching another context's data | Per-context Cognee user + API key with dataset permissions, per-context Infisical identity, per-context OAuth app and skills folder. Isolation is enforced by Cognee and Infisical, not only by gateway code; tests and the dev smoke test verify it. |
| `shared` memory | Corrupted from a connector | Connectors can add to `shared` but cannot update or delete it; fixes go through `scripts/memory_admin.py` as `hub-admin` on the host. |
| Prompt injection via memory/skills | Stored text instructing the model to exfiltrate | Server instructions state that memory and skills are data, not instructions; no tools run commands; secrets can only go to allow-listed hosts. Review what you save and what goes into your skills repo. |
| Admin UIs (Vaultwarden, Infisical, dashboard) | Exposure to the internet | Bound to `127.0.0.1`, published only with `tailscale serve` (tailnet-only, never Funnel). The dashboard validates the `Host` header and redacts every response against `.env` values. |
| Tailnet | A public sidecar pivoting into your devices | Tailscale ACL snippet: Funnel only for `tag:hub-public`, and no rule with `src: tag:hub-public`. |
| Skills repo | Write access abused | `skills-sync` uses a read-only deploy key; the validator rejects secret-shaped text. |
| Backups | Data loss / backup theft | restic (encrypted) with an optional offsite copy via rclone (`drive.file` scope). Keep the restic password outside the host. |

## Out of scope / your responsibility

- Compromise of the host machine, your GitHub account (use a passkey or 2FA) or your Tailscale account.
- What the AI providers do with the conversation content they receive (search results are sent to them).
- Keeping images up to date (`docker compose pull`) and rotating credentials.

## Hardening checklist

- Full-disk encryption on the host; GitHub with passkey/2FA.
- `VW_SIGNUPS_ALLOWED=false` after creating your Vaultwarden account.
- Delete Infisical's *Instance Admin Identity* after bootstrap.
- Never run `tailscale funnel` on the host itself; only the `hub-*` sidecars are public.
- Keep `HUB_ALLOWED_GITHUB_LOGINS` to your own login(s).
