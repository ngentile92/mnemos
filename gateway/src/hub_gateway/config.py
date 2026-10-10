"""Configuración del gateway leída de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .contexts import CONTEXTS


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise ConfigError(f"Falta la variable de entorno {name}")
    return value


def _is_local_url(url: str | None) -> bool:
    if not url:
        return True
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


@dataclass
class Settings:
    context: str
    public_url: str
    dev_no_auth: bool
    allowed_logins: frozenset[str]
    github_client_id: str | None
    github_client_secret: str | None
    jwt_signing_key: str | None
    storage_encryption_key: str | None
    data_dir: str
    cognee_url: str
    cognee_api_key: str | None
    datasets_file: str
    skills_root: str
    secret_policy_file: str
    infisical_url: str | None
    infisical_client_id: str | None
    infisical_client_secret: str | None
    infisical_environment: str
    host: str
    port: int
    auth_provider: str = "github"   # github (default) | local (built-in login, no GitHub OAuth app)
    local_user: str | None = None
    local_password_hash: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        context = _env("HUB_CONTEXT", required=True)
        if context not in CONTEXTS:
            raise ConfigError(f"HUB_CONTEXT inválido: {context!r}. Opciones: {', '.join(CONTEXTS)}")
        dev = _env("HUB_DEV_NO_AUTH", "0") == "1"
        public_url = _env("HUB_PUBLIC_URL", "http://localhost:8000")
        if dev and not _is_local_url(public_url):
            # Guardia: el modo sin auth nunca puede arrancar con una URL pública.
            raise ConfigError(
                "HUB_DEV_NO_AUTH=1 solo se permite con HUB_PUBLIC_URL en localhost. "
                "Nunca desactives la auth en una instancia expuesta por Funnel."
            )
        logins = frozenset(
            u.strip().lower() for u in (_env("HUB_ALLOWED_GITHUB_LOGINS", "") or "").split(",") if u.strip()
        )
        s = cls(
            context=context,
            public_url=public_url,
            dev_no_auth=dev,
            allowed_logins=logins,
            github_client_id=_env("GITHUB_CLIENT_ID"),
            github_client_secret=_env("GITHUB_CLIENT_SECRET"),
            jwt_signing_key=_env("HUB_JWT_SIGNING_KEY"),
            storage_encryption_key=_env("HUB_STORAGE_ENCRYPTION_KEY"),
            data_dir=_env("HUB_DATA_DIR", "/data"),
            cognee_url=_env("COGNEE_URL", "http://cognee:8000").rstrip("/"),
            cognee_api_key=_env("COGNEE_API_KEY"),
            datasets_file=_env("HUB_DATASETS_FILE", "/config/cognee-datasets.json"),
            skills_root=_env("HUB_SKILLS_ROOT", "/skills"),
            secret_policy_file=_env("HUB_SECRET_POLICY_FILE", "/config/secret-policy.yaml"),
            infisical_url=_env("INFISICAL_URL"),
            infisical_client_id=_env("INFISICAL_CLIENT_ID"),
            infisical_client_secret=_env("INFISICAL_CLIENT_SECRET"),
            infisical_environment=_env("INFISICAL_ENVIRONMENT", "prod"),
            host=_env("HUB_BIND_HOST", "0.0.0.0"),
            port=int(_env("HUB_PORT", "8000")),
            auth_provider=(_env("HUB_AUTH_PROVIDER", "github") or "github").strip().lower(),
            local_user=(_env("HUB_LOCAL_USER") or "").strip().lower() or None,
            local_password_hash=_env("HUB_LOCAL_PASSWORD_HASH"),
        )
        if s.auth_provider not in ("github", "local"):
            raise ConfigError(f"HUB_AUTH_PROVIDER inválido: {s.auth_provider!r} (github | local)")
        if s.auth_provider == "local" and not logins and s.local_user:
            s.allowed_logins = frozenset({s.local_user})  # the only account of the built-in login
        if not dev:
            need = ({"HUB_LOCAL_USER": s.local_user, "HUB_LOCAL_PASSWORD_HASH": s.local_password_hash,
                     "HUB_STORAGE_ENCRYPTION_KEY": s.storage_encryption_key}
                    if s.auth_provider == "local" else
                    {"GITHUB_CLIENT_ID": s.github_client_id, "GITHUB_CLIENT_SECRET": s.github_client_secret,
                     "HUB_JWT_SIGNING_KEY": s.jwt_signing_key,
                     "HUB_STORAGE_ENCRYPTION_KEY": s.storage_encryption_key})
            missing = [n for n, v in need.items() if not v]
            if missing:
                raise ConfigError("Faltan variables para OAuth: " + ", ".join(missing))
            if "SIN-TAILNET" in (public_url or ""):
                raise ConfigError("TS_TAILNET no está definido en .env (HUB_PUBLIC_URL inválida)")
            if not s.cognee_api_key:
                raise ConfigError("Falta COGNEE_API_KEY: corré scripts/bootstrap_cognee.py")
            if not s.allowed_logins:
                raise ConfigError("HUB_ALLOWED_GITHUB_LOGINS está vacío: nadie podría entrar (o entraría cualquiera).")
        return s
