"""Optional passkeys (WebAuthn) for the built-in login (hubs with HUB_AUTH_PROVIDER=local and the router).

Off unless HUB_PASSKEYS=1. The password stays: enrolling a passkey requires user + password, and the password
form is always shown. Credentials live in the provider's encrypted store (collection "passkeys").
RP id = host of the public URL, so a passkey works only on the hub where it was enrolled.

Login flow: /passkey/login/begin (txn) → browser get() → /passkey/login/finish verifies the assertion and marks
the pending authorization as passkey-verified → /passkey/continue?txn= runs the normal post-login step
(code redirect on hubs, consent page on the router).
"""

from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

CHALLENGE_TTL = 300

PASSKEY_JS = r"""
"use strict";
const b2u = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const u2b = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
async function pj(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || "failed"); return j;
}
function say(t) { const e = document.getElementById("pk-msg"); if (e) e.textContent = t; }
const login = document.getElementById("pk-login");
if (login) login.addEventListener("click", async () => {
  try {
    const txn = login.dataset.txn, o = await pj("/passkey/login/begin", { txn });
    o.challenge = u2b(o.challenge); (o.allowCredentials || []).forEach((c) => (c.id = u2b(c.id)));
    const c = await navigator.credentials.get({ publicKey: o });
    const r = await pj("/passkey/login/finish", { txn, credential: { id: c.id, rawId: b2u(c.rawId), type: c.type, response: {
      authenticatorData: b2u(c.response.authenticatorData), clientDataJSON: b2u(c.response.clientDataJSON),
      signature: b2u(c.response.signature), userHandle: c.response.userHandle ? b2u(c.response.userHandle) : null } } });
    location.href = r.next;
  } catch (e) { say("Passkey sign-in failed: " + e.message); }
});
const enroll = document.getElementById("pk-enroll");
if (enroll) enroll.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const f = new FormData(enroll), o = await pj("/passkey/enroll/begin", { username: f.get("username"), password: f.get("password") });
    const cid = o.cid; delete o.cid;
    o.challenge = u2b(o.challenge); o.user.id = u2b(o.user.id); (o.excludeCredentials || []).forEach((c) => (c.id = u2b(c.id)));
    const c = await navigator.credentials.create({ publicKey: o });
    await pj("/passkey/enroll/finish", { cid, name: f.get("label") || "", credential: { id: c.id, rawId: b2u(c.rawId), type: c.type, response: {
      attestationObject: b2u(c.response.attestationObject), clientDataJSON: b2u(c.response.clientDataJSON) } } });
    say("Passkey added. Next time choose 'Use a passkey'.");
  } catch (e) { say("Could not add the passkey: " + e.message); }
});
"""


class PasskeyMixin:
    """Mixed into LocalOAuthProvider. Needs: self.store, self.user, self.password_hash, self.base_url, _page,
    _locked/_fail, _after_login."""

    passkeys_enabled: bool = False

    def _rp(self) -> tuple[str, str]:
        u = urlparse(str(self.base_url))
        return u.hostname or "localhost", f"{u.scheme}://{u.netloc}"

    async def _creds(self) -> list[dict]:
        d = await self.store.get("all", collection="passkeys")
        return list((d or {}).get("creds", []))

    async def _save_creds(self, creds: list[dict]) -> None:
        await self.store.put("all", {"creds": creds}, collection="passkeys")

    async def passkey_button(self, txn: str) -> str:
        if not self.passkeys_enabled or not await self._creds():
            return ""
        import html
        return (f'<button type="button" id="pk-login" data-txn="{html.escape(txn)}">Use a passkey</button>'
                '<p class="m" id="pk-msg"></p><script src="/passkey.js"></script>')

    @staticmethod
    def _err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code, headers={"Cache-Control": "no-store"})

    async def passkey_js(self, request: Request) -> Response:
        return PlainTextResponse(PASSKEY_JS, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ enrollment (password required)
    async def enroll_page(self, request: Request) -> Response:
        return self._page(scripts=True, body="""<h2>Add a passkey</h2>
<p class="m">Confirm your user and password; your device then creates a passkey for this hub only. The password keeps working.</p>
<form id="pk-enroll"><input name="username" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="user" required>
<input name="password" type="password" autocomplete="current-password" placeholder="password" required>
<input name="label" placeholder="name for this passkey (optional)" maxlength="60">
<button type="submit">Create passkey</button></form><p class="m" id="pk-msg"></p><script src="/passkey.js"></script>""")

    async def enroll_begin(self, request: Request) -> Response:
        import hmac

        from webauthn import generate_registration_options, options_to_json
        from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                              ResidentKeyRequirement, UserVerificationRequirement)

        from .local_auth import verify_password
        if msg := await self._locked_msg():
            return self._err(msg, 429)
        try:
            body = await request.json()
        except Exception:
            return self._err("bad request")
        user = self.norm_user(body.get("username"))
        ok = verify_password(str(body.get("password", "")), self.password_hash or "")
        if not (ok and self.user and hmac.compare_digest(user.encode(), self.user.encode())):
            await self._fail()
            msg = await self._locked_msg()
            return self._err(msg or "Wrong user or password.", 429 if msg else 401)
        rp_id, _ = self._rp()
        creds = await self._creds()
        opts = generate_registration_options(
            rp_id=rp_id, rp_name="Mnemos", user_name=self.user, user_id=self.user.encode(),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb(c["id"])) for c in creds],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.PREFERRED))
        cid = secrets.token_urlsafe(24)
        await self.store.put(cid, {"challenge": _b(opts.challenge)}, collection="pk_challenges", ttl=CHALLENGE_TTL)
        return JSONResponse({**json.loads(options_to_json(opts)), "cid": cid}, headers={"Cache-Control": "no-store"})

    async def enroll_finish(self, request: Request) -> Response:
        from webauthn import verify_registration_response
        try:
            body = await request.json()
            cid = str(body.get("cid", ""))
            ch = await self.store.get(cid, collection="pk_challenges") if cid else None
            if not ch:
                return self._err("expired, start again")
            await self.store.delete(cid, collection="pk_challenges")
            rp_id, origin = self._rp()
            v = verify_registration_response(credential=body["credential"], expected_challenge=_unb(ch["challenge"]),
                                             expected_rp_id=rp_id, expected_origin=origin)
        except Exception:
            return self._err("passkey could not be verified")
        creds = await self._creds()
        creds.append({"id": _b(v.credential_id), "public_key": _b(v.credential_public_key), "sign_count": v.sign_count,
                      "name": str(body.get("name", ""))[:60]})
        await self._save_creds(creds)
        return JSONResponse({"ok": True, "passkeys": len(creds)})

    # ------------------------------------------------------------ sign-in
    async def login_begin(self, request: Request) -> Response:
        from webauthn import generate_authentication_options, options_to_json
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
        try:
            txn = str((await request.json()).get("txn", ""))
        except Exception:
            return self._err("bad request")
        pending = await self.store.get(txn, collection="pending") if txn else None
        creds = await self._creds()
        if not pending or not creds:
            return self._err("expired, start again from your assistant")
        rp_id, _ = self._rp()
        opts = generate_authentication_options(
            rp_id=rp_id, allow_credentials=[PublicKeyCredentialDescriptor(id=_unb(c["id"])) for c in creds],
            user_verification=UserVerificationRequirement.PREFERRED)
        await self.store.put("pk:" + txn, {"challenge": _b(opts.challenge)}, collection="pk_challenges", ttl=CHALLENGE_TTL)
        return JSONResponse(json.loads(options_to_json(opts)), headers={"Cache-Control": "no-store"})

    async def login_finish(self, request: Request) -> Response:
        from webauthn import verify_authentication_response
        if msg := await self._locked_msg():
            return self._err(msg, 429)
        try:
            body = await request.json()
            txn = str(body.get("txn", ""))
            pending = await self.store.get(txn, collection="pending") if txn else None
            ch = await self.store.get("pk:" + txn, collection="pk_challenges") if txn else None
            if not pending or not ch:
                return self._err("expired, start again from your assistant")
            await self.store.delete("pk:" + txn, collection="pk_challenges")
            cred = body["credential"]
            creds = await self._creds()
            match = next((c for c in creds if c["id"] == str(cred.get("rawId") or cred.get("id")).rstrip("=")), None)
            if not match:
                raise ValueError("unknown passkey")
            rp_id, origin = self._rp()
            v = verify_authentication_response(
                credential=cred, expected_challenge=_unb(ch["challenge"]), expected_rp_id=rp_id, expected_origin=origin,
                credential_public_key=_unb(match["public_key"]), credential_current_sign_count=match["sign_count"])
        except Exception:
            await self._fail()
            return self._err("passkey not accepted", 401)
        match["sign_count"] = v.new_sign_count
        await self._save_creds(creds)
        await self.store.put(txn, {**pending, "passkey_login": self.user}, collection="pending", ttl=300)
        return JSONResponse({"next": f"/passkey/continue?txn={txn}"})

    async def passkey_continue(self, request: Request) -> Response:
        from .local_auth import EXPIRED
        txn = request.query_params.get("txn", "")
        pending = await self.store.get(txn, collection="pending") if txn else None
        if not pending or not pending.get("passkey_login"):
            return self._page(EXPIRED, 400)
        login = pending.pop("passkey_login")
        await self.store.put(txn, pending, collection="pending", ttl=300)
        return await self._after_login(txn, pending, login)

    def passkey_routes(self) -> list[Route]:
        if not self.passkeys_enabled:
            return []
        return [Route("/passkey.js", self.passkey_js, methods=["GET"]),
                Route("/passkey/enroll", self.enroll_page, methods=["GET"]),
                Route("/passkey/enroll/begin", self.enroll_begin, methods=["POST"]),
                Route("/passkey/enroll/finish", self.enroll_finish, methods=["POST"]),
                Route("/passkey/login/begin", self.login_begin, methods=["POST"]),
                Route("/passkey/login/finish", self.login_finish, methods=["POST"]),
                Route("/passkey/continue", self.passkey_continue, methods=["GET"])]


def _b(x: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(x).decode().rstrip("=")


def _unb(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def enabled_from_env(env: Any = None) -> bool:
    import os
    return (env or os.environ).get("HUB_PASSKEYS", "").strip().lower() in ("1", "true", "yes", "on")
