# Sign in to the Mnemos router with Google (step by step)

Optional. The router's own password (and passkeys) keep working; Google is one more button on the sign-in page.
Only the Google accounts you list are accepted, and only with a **verified** email. Mnemos asks Google for
`openid email` and nothing else: no Gmail, Drive or contacts access, no refresh token from Google.

Legend: ✅ verified by Mnemos tests or live checks · 👆 needs you in Google Cloud Console (not verifiable without an account).

## 0. What you need
- The router running and reachable (`scripts/mnemos expose --check` shows `mnemos.<tailnet>.ts.net` OK). ✅
- A Google account. A free Google Cloud project is enough; no billing. 👆

## 1. Create the app in Google Auth Platform 👆
1. Open <https://console.cloud.google.com/auth/overview> → pick or create a project (e.g. `mnemos-login`).
2. **Get started** → App name `Mnemos`, user support email = your address → **Next**.
3. **Audience**: *External* → **Next** → contact email → agree → **Create**.
4. **Audience** page → *Publishing status: Testing* (leave it) → **Test users → Add users** → your Google address.
   (For sign-in with only `openid`/`email`, Google does not require verification; Testing is fine for personal use.)
5. **Data Access** → **Add or remove scopes** → tick `openid` and `.../auth/userinfo.email` only → **Update** → **Save**.

## 2. Create the OAuth client 👆
1. **Clients → Create client** → *Application type*: **Web application**, name `Mnemos router`.
2. *Authorized JavaScript origins*: leave empty.
3. *Authorized redirect URIs* → **Add URI** → exactly (https, no trailing slash):
   `https://mnemos.<your-tailnet>.ts.net/oidc/google/callback` ✅ (this is the path the router sends; tested)
4. **Create**. Copy the **Client ID** and **Client secret** now (the secret is shown once; store it in Vaultwarden).

If Google rejects the redirect URI's domain, add `ts.net`'s parent for your tailnet under **Branding → Authorized
domains** (`<your-tailnet>.ts.net`) and retry. 👆 (not verified: depends on Google's checks for your project)

## 3. Configure Mnemos ✅
In `~/code/mnemos/.env` (back it up first):

```bash
MNEMOS_GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
MNEMOS_GOOGLE_CLIENT_SECRET=GOCSPX-...
MNEMOS_ROUTER_ALLOWED_EMAILS=you@gmail.com        # comma-separated; mandatory, the router refuses to start without it
```

Then `scripts/update.sh origin/main`. The router only reads these at start.

## 4. Check ✅ / 👆
1. ✅ In an assistant, connect (or reconnect) `https://mnemos.<tailnet>.ts.net/mcp`: the sign-in page now shows
   **Sign in with Google** under the password form.
2. 👆 Click it → choose your account → Google shows "Mnemos wants to: see your email address" → Continue.
3. ✅ You land on the Mnemos consent page (pick contexts) → Allow → back in the assistant.
4. A different Google account, or one whose email Google has not verified, gets "not allowed" ✅ (tested).

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `Error 400: redirect_uri_mismatch` | The URI in step 2.3 must match exactly (`https`, host, `/oidc/google/callback`, no `/` at the end). |
| `Access blocked: app has not completed verification` | Your address is not in **Audience → Test users**. |
| Router does not start, log says `needs MNEMOS_ROUTER_ALLOWED_EMAILS` | Add the allowlist (step 3). |
| "This sign-in link expired" after Google | More than 10 minutes between the click and the callback, or the router restarted: start again from the assistant. |

To turn it off: remove the two `MNEMOS_GOOGLE_*` lines and run `scripts/update.sh`; delete the client in Google
Cloud (**Clients → trash**) if you will not use it again.
