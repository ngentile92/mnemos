"""hub-router settings (env). See docs/router.md."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..config import ConfigError
from ..contexts import CONTEXTS


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _set(name: str) -> frozenset[str]:
    return frozenset(x.strip().lower() for x in (_env(name, "") or "").split(",") if x.strip())


@dataclass
class Backend:
    context: str
    url: str          # http://ts-<ctx>:8100/mcp (internal listener, Docker network only)
    key: str          # HUB_INTERNAL_KEY_<CTX>


@dataclass
class RouterSettings:
    public_url: str
    data_dir: str
    storage_key: str
    contexts: list[str]
    backends: dict[str, Backend] = field(default_factory=dict)
    local_user: str | None = None
    local_password_hash: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    allowed_emails: frozenset[str] = frozenset()
    github_client_id: str | None = None
    github_client_secret: str | None = None
    allowed_github: frozenset[str] = frozenset()
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "RouterSettings":
        url = _env("MNEMOS_ROUTER_PUBLIC_URL")
        if not url or not url.startswith("https://"):
            raise ConfigError("MNEMOS_ROUTER_PUBLIC_URL must be the https URL of the router (https://mnemos.<tailnet>)")
        key = _env("MNEMOS_ROUTER_STORAGE_KEY")
        if not key:
            raise ConfigError("MNEMOS_ROUTER_STORAGE_KEY (Fernet key) is required: scripts/init_env.py generates it")
        names = [c.strip() for c in (_env("MNEMOS_ROUTER_CONTEXTS", "") or "").split(",") if c.strip()] or list(CONTEXTS)
        bad = [c for c in names if c not in CONTEXTS]
        if bad:
            raise ConfigError(f"MNEMOS_ROUTER_CONTEXTS: unknown contexts {bad}")
        backends = {}
        for c in names:
            s = c.upper().replace("-", "_")
            k = _env(f"HUB_INTERNAL_KEY_{s}")
            if k:
                backends[c] = Backend(c, _env(f"MNEMOS_BACKEND_{s}", f"http://ts-{c}:8100/mcp"), k)
        s = cls(public_url=url.rstrip("/"), data_dir=_env("MNEMOS_ROUTER_DATA_DIR", "/data") or "/data",
                storage_key=key, contexts=names, backends=backends,
                local_user=(_env("HUB_LOCAL_USER") or "").strip().lower() or None,
                local_password_hash=_env("HUB_LOCAL_PASSWORD_HASH"),
                google_client_id=_env("MNEMOS_GOOGLE_CLIENT_ID"), google_client_secret=_env("MNEMOS_GOOGLE_CLIENT_SECRET"),
                allowed_emails=_set("MNEMOS_ROUTER_ALLOWED_EMAILS"),
                github_client_id=_env("MNEMOS_ROUTER_GITHUB_CLIENT_ID"),
                github_client_secret=_env("MNEMOS_ROUTER_GITHUB_CLIENT_SECRET"),
                allowed_github=_set("HUB_ALLOWED_GITHUB_LOGINS"),
                host=_env("MNEMOS_ROUTER_BIND_HOST", "0.0.0.0") or "0.0.0.0",
                port=int(_env("MNEMOS_ROUTER_PORT", "8000") or "8000"))
        if s.google_client_id and not s.allowed_emails:
            raise ConfigError("Google sign-in needs MNEMOS_ROUTER_ALLOWED_EMAILS (otherwise any Google account works)")
        if s.github_client_id and not s.allowed_github:
            raise ConfigError("GitHub sign-in needs HUB_ALLOWED_GITHUB_LOGINS")
        return s

    def idps(self):
        from .auth import ExternalIdP

        out = []
        if self.google_client_id and self.google_client_secret:
            out.append(ExternalIdP.google(self.google_client_id, self.google_client_secret, self.allowed_emails))
        if self.github_client_id and self.github_client_secret:
            out.append(ExternalIdP.github(self.github_client_id, self.github_client_secret, self.allowed_github))
        return out
