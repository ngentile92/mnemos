import base64
import urllib.parse

import httpx
import pytest

from hub_gateway.secrets import (
    SecretBroker, SecretPolicyError, load_policy, parse_section, redact, secret_variants, validate_url,
)

SECRET = "s3cr3t-VALUE_xyz+/="


async def public_resolver(host):
    return ["93.184.216.34"]


class FakeFetcher:
    def __init__(self, value=SECRET):
        self.value = value
        self.calls = []

    async def fetch(self, spec):
        self.calls.append(spec.name)
        return self.value


def broker_for(policy_file, context, handler, fetcher=None, resolver=public_resolver):
    return SecretBroker(load_policy(str(policy_file), context), fetcher or FakeFetcher(), resolver=resolver,
                        transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- policy

def test_policy_is_per_context(policy_file):
    assert set(load_policy(str(policy_file), "work")) == {"HUBSPOT_TOKEN"}
    assert set(load_policy(str(policy_file), "personal")) == {"GITHUB_PAT"}
    assert load_policy(str(policy_file), "desconocido") == {}


@pytest.mark.parametrize("raw,msg", [
    ({"project": "p", "allowed_hosts": []}, "vacío"),
    ({"project": "p", "allowed_hosts": ["*.github.com"]}, "exactos"),
    ({"project": "p", "allowed_hosts": ["api.github.com:8443"]}, "exactos"),
    ({"project": "p", "allowed_hosts": ["a.com"], "inject": {"format": "Bearer"}}, "value"),
    ({"allowed_hosts": ["a.com"]}, "project"),
    ({"project": "p", "allowed_hosts": ["a.com"], "methods": ["CONNECT"]}, "métodos"),
])
def test_policy_validation(raw, msg):
    with pytest.raises(SecretPolicyError, match=msg):
        parse_section({"X": raw})


def test_list_never_contains_values(policy_file):
    b = broker_for(policy_file, "personal", lambda r: httpx.Response(200))
    listed = b.list()
    assert listed[0]["name"] == "GITHUB_PAT"
    assert SECRET not in repr(listed)
    assert set(listed[0]) == {"name", "description", "allowed_hosts", "allowed_methods", "injected_as"}


# ---------------------------------------------------------------- host allowlist

@pytest.mark.parametrize("url,msg", [
    ("http://api.github.com/user", "https"),
    ("https://evil.com/user", "no permitido"),
    ("https://api.github.com.evil.com/user", "no permitido"),
    ("https://evilapi.github.com/user", "no permitido"),
    ("https://user:pw@api.github.com/user", "credenciales"),
    ("https://api.github.com@evil.com/", "credenciales"),
    ("https://api.github.com:8443/user", "443"),
    ("https://140.82.112.5/user", "IPs"),
    ("https:///user", "host"),
    ("ftp://api.github.com/", "https"),
])
async def test_url_rejections(policy_file, url, msg):
    spec = load_policy(str(policy_file), "personal")["GITHUB_PAT"]
    with pytest.raises(SecretPolicyError, match=msg):
        await validate_url(url, spec, public_resolver)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "172.18.0.3", "169.254.169.254",
                                "100.101.102.103", "::1", "fd7a:115c:a1e0::1", "::ffff:10.0.0.1", "0.0.0.0"])
async def test_dns_to_private_or_tailnet_rejected(policy_file, ip):
    spec = load_policy(str(policy_file), "personal")["GITHUB_PAT"]

    async def resolver(host):
        return ["140.82.112.5", ip]

    with pytest.raises(SecretPolicyError, match="no permitida"):
        await validate_url("https://api.github.com/user", spec, resolver)


async def test_valid_url_and_case_insensitive_host(policy_file):
    spec = load_policy(str(policy_file), "personal")["GITHUB_PAT"]
    assert await validate_url("https://API.GitHub.com:443/user?x=1", spec, public_resolver) == "api.github.com"


# ---------------------------------------------------------------- redacción

def test_variants_and_redaction():
    v = secret_variants(SECRET, f"Bearer {SECRET}")
    text = " | ".join([
        SECRET,
        base64.b64encode(SECRET.encode()).decode(),
        urllib.parse.quote(SECRET, safe=""),
        urllib.parse.quote_plus(SECRET),
        base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip("="),
    ])
    out = redact(text, v, "X")
    assert SECRET not in out
    assert base64.b64encode(SECRET.encode()).decode() not in out
    assert out.count("[REDACTED:X]") == 5


# ---------------------------------------------------------------- broker end-to-end (transporte simulado)

async def test_injection_and_response_redaction(policy_file):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        echoed = base64.b64encode(SECRET.encode()).decode()
        return httpx.Response(200, headers={"X-Echo": SECRET, "Set-Cookie": "a=b", "Content-Type": "application/json"},
                              json={"token": SECRET, "b64": echoed, "ok": True})

    fetcher = FakeFetcher()
    b = broker_for(policy_file, "work", handler, fetcher)
    res = await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/crm/v3/objects/contacts")
    assert seen["auth"] == f"Bearer {SECRET}"  # inyectado del lado del servidor
    assert res["status"] == 200
    dump = repr(res)
    assert SECRET not in dump and base64.b64encode(SECRET.encode()).decode() not in dump
    assert "x-echo" not in {k.lower() for k in res["headers"]}  # headers no seguros se descartan
    assert "set-cookie" not in {k.lower() for k in res["headers"]}
    assert "[REDACTED:HUBSPOT_TOKEN]" in res["body"]
    assert fetcher.calls == ["HUBSPOT_TOKEN"]


async def test_secret_split_at_truncation_boundary_is_not_leaked(policy_file, monkeypatch):
    import hub_gateway.secrets as s
    monkeypatch.setattr(s, "MAX_BODY_BYTES", 30)
    body = "x" * 25 + SECRET + "y" * 10

    b = broker_for(policy_file, "work", lambda r: httpx.Response(200, text=body))
    res = await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/x")
    assert SECRET[:5] not in res["body"]
    assert res["truncated"] is True


async def test_redirects_not_followed(policy_file):
    def handler(request):
        return httpx.Response(302, headers={"Location": f"https://evil.com/?t={SECRET}"})

    b = broker_for(policy_file, "work", handler)
    res = await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/x")
    assert res["status"] == 302 and res["note"] == "redirección no seguida"
    assert SECRET not in res["headers"]["location"]


async def test_secret_from_other_context_is_unknown(policy_file):
    b = broker_for(policy_file, "personal", lambda r: httpx.Response(200))
    with pytest.raises(SecretPolicyError, match="no existe en este contexto"):
        await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/x")


async def test_host_not_allowed_never_fetches_secret(policy_file):
    fetcher = FakeFetcher()
    b = broker_for(policy_file, "work", lambda r: httpx.Response(200), fetcher)
    with pytest.raises(SecretPolicyError):
        await b.request("HUBSPOT_TOKEN", "POST", "https://example.com/steal", body="x")
    assert fetcher.calls == []


async def test_method_restriction(policy_file):
    b = broker_for(policy_file, "personal", lambda r: httpx.Response(200))
    with pytest.raises(SecretPolicyError, match="método"):
        await b.request("GITHUB_PAT", "DELETE", "https://api.github.com/repos/x/y")


@pytest.mark.parametrize("headers", [{"Authorization": "Bearer mine"}, {"authorization": "x"}, {"Host": "evil.com"},
                                     {"Cookie": "a=b"}])
async def test_model_cannot_override_sensitive_headers(policy_file, headers):
    b = broker_for(policy_file, "work", lambda r: httpx.Response(200))
    with pytest.raises(SecretPolicyError, match="header"):
        await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/x", headers=headers)


async def test_request_containing_secret_is_blocked(policy_file):
    b = broker_for(policy_file, "work", lambda r: httpx.Response(200))
    with pytest.raises(SecretPolicyError, match="contiene el valor"):
        await b.request("HUBSPOT_TOKEN", "POST", "https://api.hubapi.com/x", body=f'{{"t":"{SECRET}"}}')


async def test_no_infisical_configured(policy_file):
    b = SecretBroker(load_policy(str(policy_file), "work"), None, resolver=public_resolver)
    with pytest.raises(SecretPolicyError, match="Infisical"):
        await b.request("HUBSPOT_TOKEN", "GET", "https://api.hubapi.com/x")


def test_policy_alias_fetches_real_infisical_key(monkeypatch):
    import time as _time
    from types import SimpleNamespace

    from hub_gateway.secrets import InfisicalFetcher, parse_section

    spec = parse_section({"ALIAS": {"project": "p", "path": "/x", "key": "REAL_NAME",
                                    "allowed_hosts": ["api.example.com"]}})["ALIAS"]
    assert spec.name == "ALIAS" and spec.infisical_key == "REAL_NAME"
    seen = {}

    def get_secret_by_name(**kw):
        seen.update(kw)
        return SimpleNamespace(secretValue="v")

    f = InfisicalFetcher("http://infisical:8080", "id", "secret", "prod")
    f._client = SimpleNamespace(secrets=SimpleNamespace(get_secret_by_name=get_secret_by_name))
    f._logged_at = _time.monotonic()
    assert f._get(spec) == "v"
    assert seen["secret_name"] == "REAL_NAME" and seen["secret_path"] == "/x"
    with pytest.raises(Exception):
        parse_section({"B": {"project": "p", "key": "../x", "allowed_hosts": ["a.example.com"]}})
