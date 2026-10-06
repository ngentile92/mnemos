"""Uso de secretos por inyección: el valor nunca vuelve al modelo.

Flujo de secret_http_request:
  1. el secreto tiene que estar en la policy del contexto activo;
  2. la URL tiene que ser https, host exacto en allowed_hosts, puerto 443, sin credenciales
     embebidas, y el host no puede resolver a IPs privadas/loopback/link-local/CGNAT (tailnet);
  3. se trae el valor de Infisical y se inyecta en el header indicado;
  4. el request se hace sin seguir redirecciones, con timeout;
  5. la respuesta se redacta (valor y variantes) y se trunca.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
import socket
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import httpx
import yaml

MAX_BODY_BYTES = 64 * 1024
MAX_REQUEST_BODY_BYTES = 256 * 1024
REQUEST_TIMEOUT = 20.0
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "date",
    "etag",
    "last-modified",
    "location",
    "retry-after",
    "x-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}
# Headers que el modelo no puede fijar.
FORBIDDEN_REQUEST_HEADERS = {"host", "cookie", "proxy-authorization", "content-length", "transfer-encoding"}
# 100.64.0.0/10 es CGNAT, lo usa Tailscale para las IPs del tailnet.
EXTRA_BLOCKED_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # rango IPv6 de Tailscale
]


class SecretPolicyError(ValueError):
    """La operación viola la policy (no revela valores)."""


@dataclass(frozen=True)
class SecretSpec:
    name: str
    project: str | None
    project_id: str | None
    path: str
    description: str
    allowed_hosts: frozenset[str]
    header: str
    format: str = "{value}"
    environment: str | None = None
    methods: frozenset[str] = field(default_factory=lambda: frozenset(ALLOWED_METHODS))
    # nombre del secreto en Infisical si difiere del nombre de la policy (alias; default = name)
    key: str | None = None

    @property
    def infisical_key(self) -> str:
        return self.key or self.name

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_hosts": sorted(self.allowed_hosts),
            "allowed_methods": sorted(self.methods),
            "injected_as": f"header {self.header}",
        }


def load_policy(path: str, context: str) -> dict[str, SecretSpec]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    section = data.get(context) or {}
    return parse_section(section)


def parse_section(section: dict[str, Any]) -> dict[str, SecretSpec]:
    out: dict[str, SecretSpec] = {}
    for name, raw in section.items():
        raw = raw or {}
        inject = raw.get("inject") or {}
        hosts = frozenset(h.strip().lower() for h in raw.get("allowed_hosts", []) if h.strip())
        if not hosts:
            raise SecretPolicyError(f"{name}: allowed_hosts vacío")
        if any("*" in h or "/" in h or ":" in h for h in hosts):
            raise SecretPolicyError(f"{name}: allowed_hosts admite solo hosts exactos (sin comodines, puertos ni rutas)")
        fmt = inject.get("format", "{value}")
        if "{value}" not in fmt:
            raise SecretPolicyError(f"{name}: inject.format debe contener {{value}}")
        methods = frozenset(m.upper() for m in raw.get("methods", ALLOWED_METHODS))
        if not methods <= ALLOWED_METHODS:
            raise SecretPolicyError(f"{name}: métodos inválidos {sorted(methods - ALLOWED_METHODS)}")
        if not raw.get("project") and not raw.get("project_id"):
            raise SecretPolicyError(f"{name}: falta project (slug) o project_id")
        key = raw.get("key")
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(key)):
            raise SecretPolicyError(f"{name}: key inválida")
        out[name] = SecretSpec(
            name=name,
            project=raw.get("project"),
            project_id=raw.get("project_id"),
            path=raw.get("path", "/"),
            description=raw.get("description", ""),
            allowed_hosts=hosts,
            header=inject.get("header", "Authorization"),
            format=fmt,
            environment=raw.get("environment"),
            methods=methods,
            key=str(key) if key is not None else None,
        )
    return out


def _ip_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or any(addr in n for n in EXTRA_BLOCKED_NETS)
    )


Resolver = Callable[[str], Awaitable[list[str]]]


async def default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


async def validate_url(url: str, spec: SecretSpec, resolver: Resolver = default_resolver) -> str:
    """Valida la URL contra la policy. Devuelve el host normalizado o levanta SecretPolicyError."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise SecretPolicyError("URL inválida") from exc
    if parts.scheme != "https":
        raise SecretPolicyError("solo se permite https")
    if parts.username or parts.password or "@" in parts.netloc:
        raise SecretPolicyError("la URL no puede llevar credenciales")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise SecretPolicyError("URL sin host")
    try:
        port = parts.port
    except ValueError as exc:
        raise SecretPolicyError("puerto inválido") from exc
    if port not in (None, 443):
        raise SecretPolicyError("solo se permite el puerto 443")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise SecretPolicyError("no se permiten IPs literales")
    if host not in spec.allowed_hosts:
        raise SecretPolicyError(f"host {host!r} no permitido para {spec.name}")
    try:
        ips = await resolver(host)
    except OSError as exc:
        raise SecretPolicyError(f"no se pudo resolver {host}") from exc
    if not ips or any(_ip_blocked(ip) for ip in ips):
        raise SecretPolicyError(f"{host} resuelve a una dirección no permitida")
    return host


def secret_variants(value: str, formatted: str | None = None) -> list[str]:
    """Todas las formas en que el valor podría reaparecer en una respuesta."""
    variants = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}
    raw = value.encode()
    variants.add(base64.b64encode(raw).decode())
    variants.add(base64.urlsafe_b64encode(raw).decode())
    variants.add(base64.b64encode(raw).decode().rstrip("="))
    variants.add(base64.urlsafe_b64encode(raw).decode().rstrip("="))
    if formatted:
        variants.add(formatted)
    # Solo redactamos variantes con largo razonable para no destruir la respuesta.
    return sorted((v for v in variants if len(v) >= 4), key=len, reverse=True)


def redact(text: str, variants: list[str], label: str) -> str:
    for v in variants:
        if v in text:
            text = text.replace(v, f"[REDACTED:{label}]")
    return text


class SecretFetcher(Protocol):
    async def fetch(self, spec: SecretSpec) -> str: ...


class InfisicalFetcher:
    """Trae valores de Infisical con una machine identity (Universal Auth) del contexto."""

    def __init__(self, host: str, client_id: str, client_secret: str, environment: str = "prod",
                 token_ttl: float = 600.0) -> None:
        self.host = host
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        self.token_ttl = token_ttl
        self._client = None
        self._logged_at = 0.0
        self._lock = asyncio.Lock()

    def _login(self):
        from infisical_sdk import InfisicalSDKClient  # import perezoso: tests sin Infisical

        client = InfisicalSDKClient(host=self.host, cache_ttl=0)
        client.auth.universal_auth.login(client_id=self.client_id, client_secret=self.client_secret)
        return client

    def _get(self, spec: SecretSpec) -> str:
        if self._client is None or time.monotonic() - self._logged_at > self.token_ttl:
            self._client = self._login()
            self._logged_at = time.monotonic()
        secret = self._client.secrets.get_secret_by_name(
            secret_name=spec.infisical_key,
            environment_slug=spec.environment or self.environment,
            secret_path=spec.path,
            project_id=spec.project_id,
            project_slug=None if spec.project_id else spec.project,
        )
        return secret.secretValue

    async def fetch(self, spec: SecretSpec) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._get, spec)


class SecretBroker:
    def __init__(self, policy: dict[str, SecretSpec], fetcher: SecretFetcher | None,
                 resolver: Resolver = default_resolver,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.policy = policy
        self.fetcher = fetcher
        self.resolver = resolver
        self.transport = transport

    def list(self) -> list[dict[str, Any]]:
        return [s.public() for s in sorted(self.policy.values(), key=lambda s: s.name)]

    def spec(self, name: str) -> SecretSpec:
        spec = self.policy.get(name)
        if spec is None:
            raise SecretPolicyError(f"secreto {name!r} no existe en este contexto")
        return spec

    async def request(self, name: str, method: str, url: str, headers: dict[str, str] | None = None,
                      body: str | None = None) -> dict[str, Any]:
        spec = self.spec(name)
        method = method.upper()
        if method not in spec.methods:
            raise SecretPolicyError(f"método {method} no permitido para {name}")
        host = await validate_url(url, spec, self.resolver)
        if body is not None and len(body.encode()) > MAX_REQUEST_BODY_BYTES:
            raise SecretPolicyError("body demasiado grande")
        clean_headers: dict[str, str] = {}
        for k, v in (headers or {}).items():
            lk = k.lower()
            if lk in FORBIDDEN_REQUEST_HEADERS or lk == spec.header.lower():
                raise SecretPolicyError(f"no podés fijar el header {k}")
            clean_headers[k] = v
        if self.fetcher is None:
            raise SecretPolicyError("Infisical no está configurado en esta instancia")
        value = await self.fetcher.fetch(spec)
        if not value:
            raise SecretPolicyError(f"secreto {name} vacío en Infisical")
        formatted = spec.format.replace("{value}", value)
        variants = secret_variants(value, formatted)
        # Si el modelo intentó meter el valor (por ejemplo lo adivinó) en headers o body, se corta.
        for v in variants:
            if (body and v in body) or any(v in hv for hv in clean_headers.values()) or v in url:
                raise SecretPolicyError("el request contiene el valor del secreto; no se envía")
        clean_headers[spec.header] = formatted
        async with httpx.AsyncClient(follow_redirects=False, timeout=REQUEST_TIMEOUT,
                                     transport=self.transport, trust_env=False) as client:
            resp = await client.request(method, url, headers=clean_headers,
                                        content=body.encode() if body is not None else None)
        # Primero se redacta el cuerpo completo y después se trunca: así un valor partido
        # en el borde del truncado no puede filtrarse a medias.
        full = resp.content.decode(resp.encoding or "utf-8", errors="replace")
        redacted = redact(full, variants, name)
        out_headers = {
            k: redact(v, variants, name)
            for k, v in resp.headers.items()
            if k.lower() in SAFE_RESPONSE_HEADERS
        }
        return {
            "status": resp.status_code,
            "host": host,
            "headers": out_headers,
            "body": redacted[:MAX_BODY_BYTES],
            "truncated": len(redacted) > MAX_BODY_BYTES,
            "note": "redirección no seguida" if resp.is_redirect else None,
        }
