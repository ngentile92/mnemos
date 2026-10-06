"""scripts/remove_infisical_secrets.py: verifica destino antes de borrar y nunca imprime valores."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("remove_infisical_secrets", ROOT / "scripts" / "remove_infisical_secrets.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from import_env_secrets import Infisical  # noqa: E402

VALUE = "s3cr3t-value-never-printed"


def fake(state: dict[tuple[str, str], set[str]], deleted: list[str]):
    pids = {"hub-personal": "p1", "hub-side": "p2"}
    rev = {v: k for k, v in pids.items()}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/projects":
            return httpx.Response(200, json={"projects": [{"slug": s, "id": i} for s, i in pids.items()]})
        if req.url.path == "/api/v4/secrets" and req.method == "GET":
            key = (rev[req.url.params["projectId"]], req.url.params["secretPath"])
            return httpx.Response(200, json={"secrets": [{"secretKey": k, "secretValue": VALUE} for k in state.get(key, set())]})
        if req.url.path.startswith("/api/v4/secrets/") and req.method == "DELETE":
            body = json.loads(req.content)
            name = req.url.path.rsplit("/", 1)[1]
            state[(rev[body["projectId"]], body["secretPath"])].discard(name)
            deleted.append(name)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "no"})

    return Infisical("http://127.0.0.1:8082", "tok", transport=httpx.MockTransport(handler))


def args(**kw):
    base = dict(project="hub-personal", path="/", env="prod", name=["A_KEY", "B_KEY"],
                require_in="hub-side:/shop", apply=True, url="http://127.0.0.1:8082")
    base.update(kw)
    return argparse.Namespace(**base)


def test_plan_does_not_need_token(capsys):
    assert mod.run(args(apply=False)) == 0
    assert "Nada borrado" in capsys.readouterr().out


def test_deletes_after_verifying_destination(capsys):
    state = {("hub-personal", "/"): {"A_KEY", "B_KEY", "SMS_KEY"}, ("hub-side", "/shop"): {"A_KEY", "B_KEY"}}
    deleted: list[str] = []
    assert mod.run(args(), fake(state, deleted)) == 0
    assert deleted == ["A_KEY", "B_KEY"]
    assert state[("hub-personal", "/")] == {"SMS_KEY"}
    assert VALUE not in capsys.readouterr().out


def test_aborts_if_missing_in_destination():
    state = {("hub-personal", "/"): {"A_KEY", "B_KEY"}, ("hub-side", "/shop"): {"A_KEY"}}
    deleted: list[str] = []
    with pytest.raises(SystemExit, match="no borro nada"):
        mod.run(args(), fake(state, deleted))
    assert deleted == []
