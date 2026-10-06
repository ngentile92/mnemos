"""The example policy (config/secret-policy.example.yaml) loads cleanly in every context."""

from pathlib import Path

import pytest

from hub_gateway.contexts import CONTEXTS
from hub_gateway.secrets import load_policy

POLICY = Path(__file__).resolve().parents[2] / "config" / "secret-policy.example.yaml"


@pytest.mark.parametrize("ctx", list(CONTEXTS))
def test_example_policy_loads(ctx):
    specs = load_policy(str(POLICY), ctx)
    assert specs, f"{ctx}: empty section"
    for s in specs.values():
        assert s.allowed_hosts and all("*" not in h for h in s.allowed_hosts)


@pytest.mark.parametrize(
    ("ctx", "name", "host", "header", "fmt"),
    [
        ("work", "OPENAI_API_KEY", "api.openai.com", "Authorization", "Bearer {value}"),
        ("personal", "GITHUB_PAT_READONLY", "api.github.com", "Authorization", "Bearer {value}"),
        ("side", "OPENAI_API_KEY", "api.openai.com", "Authorization", "Bearer {value}"),
    ],
)
def test_example_secrets_have_exact_host(ctx, name, host, header, fmt):
    s = load_policy(str(POLICY), ctx)[name]
    assert s.allowed_hosts == frozenset({host})
    assert (s.header, s.format) == (header, fmt)


def test_side_secret_lives_in_project_folder():
    s = load_policy(str(POLICY), "side")["OPENAI_API_KEY"]
    assert (s.project, s.path) == ("hub-side", "/shop")
    assert load_policy(str(POLICY), "personal")["GITHUB_PAT_READONLY"].methods == frozenset({"GET"})
