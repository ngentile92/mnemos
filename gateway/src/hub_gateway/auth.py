"""Auth del gateway: OAuth 2.1 (spec MCP) con GitHub como IdP vía el OAuth proxy de FastMCP,
más una allowlist de logins de GitHub. Sin allowlist, GitHubProvider aceptaría cualquier cuenta."""

from __future__ import annotations

import os

from .config import Settings


def build_auth(settings: Settings):
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.providers.github import GitHubProvider
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    oauth_dir = os.path.join(settings.data_dir, "oauth")
    os.makedirs(oauth_dir, exist_ok=True)
    return GitHubProvider(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        base_url=settings.public_url,
        jwt_signing_key=settings.jwt_signing_key,
        # Registros de clientes y tokens persistentes y cifrados: sobreviven reinicios,
        # así los conectores no piden login de nuevo cada vez que prendés la notebook.
        client_storage=FernetEncryptionWrapper(
            key_value=FileTreeStore(data_directory=oauth_dir),
            fernet=Fernet(settings.storage_encryption_key.encode()),
        ),
        # require_authorization_consent=True (default): pantalla de consentimiento contra confused deputy.
    )


def login_from_token(token) -> str | None:
    if token is None:
        return None
    claims = getattr(token, "claims", None) or {}
    login = claims.get("login") or claims.get("preferred_username")
    return str(login).lower() if login else None


def make_owner_check(allowed: frozenset[str]):
    def only_owner(ctx) -> bool:
        return login_from_token(ctx.token) in allowed

    return only_owner
