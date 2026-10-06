"""Tests de scripts/visualize.py (vista global del grafo, solo hub-admin y solo local)."""

import datetime as dt
import importlib.util
import json
import stat
from pathlib import Path

import httpx
import pytest
import respx

from conftest import DATASETS

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "visualize.py"
spec = importlib.util.spec_from_file_location("visualize", SCRIPT)
viz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viz)

USERS = {
    "hub-admin": "00000000-0000-0000-0000-0000000000f0",
    "ctx-work": "00000000-0000-0000-0000-0000000000f1",
    "ctx-personal": "00000000-0000-0000-0000-0000000000f2",
    "ctx-side": "00000000-0000-0000-0000-0000000000f3",
}
STATE = {"datasets": DATASETS, "users": USERS}
BASE = "http://127.0.0.1:8010"


def test_select_datasets():
    assert sorted(viz.select_datasets(None)) == sorted(DATASETS)
    assert viz.select_datasets("side") == ["side_shop", "side_blog", "side_mnemos"]
    assert viz.select_datasets("personal", with_shared=True) == ["personal", "shared"]
    assert viz.select_datasets("shared") == ["shared"]
    with pytest.raises(SystemExit):
        viz.select_datasets("work-x")


def test_build_pairs_uses_owner_of_each_dataset():
    pairs = viz.build_pairs(["shared", "work", "side_blog"], STATE)
    assert pairs == [
        {"user_id": USERS["hub-admin"], "dataset_id": DATASETS["shared"]},
        {"user_id": USERS["ctx-work"], "dataset_id": DATASETS["work"]},
        {"user_id": USERS["ctx-side"], "dataset_id": DATASETS["side_blog"]},
    ]
    with pytest.raises(SystemExit):
        viz.build_pairs(["personal"], {"datasets": {}, "users": USERS})


def test_output_path():
    d = dt.date(2026, 9, 29)
    assert viz.output_path(Path("/g"), None, d) == Path("/g/global-2026-09-29.html")
    assert viz.output_path(Path("/g"), "side", d) == Path("/g/side-2026-09-29.html")


@pytest.mark.parametrize("url", ["http://cognee:8000", "https://hub-personal.tail1234.ts.net", "http://10.0.0.5:8000"])
def test_refuses_non_local_url(url):
    with pytest.raises(SystemExit):
        viz.ensure_local(url)


def _files(tmp_path: Path) -> list[str]:
    env = tmp_path / ".env"
    env.write_text("COGNEE_PW_ADMIN=pw-admin\n")
    ds = tmp_path / "ds.json"
    ds.write_text(json.dumps(STATE))
    return ["--env-file", str(env), "--datasets-file", str(ds), "--out-dir", str(tmp_path / "out"), "--no-open"]


def _health():
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(
        200, headers={"server": "uvicorn"}, json={"status": "ready", "version": "1.6.1"}))


@respx.mock
def test_refuses_to_login_if_port_is_not_cognee(tmp_path):
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(404, headers={"server": "WSGIServer/0.2"}))
    login = respx.post(f"{BASE}/api/v1/auth/login")
    with pytest.raises(SystemExit, match="no mando credenciales"):
        viz.main(_files(tmp_path))
    assert not login.called


@respx.mock
def test_global_render_writes_private_html(tmp_path):
    _health()
    login = respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "t"}))
    respx.get(f"{BASE}/api/v1/users/me").mock(
        return_value=httpx.Response(200, json={"id": USERS["hub-admin"], "is_superuser": True}))
    multi = respx.post(f"{BASE}/api/v1/visualize/multi").mock(return_value=httpx.Response(200, text="<html>g</html>"))

    path = viz.main(_files(tmp_path))

    assert login.calls[0].request.content.startswith(b"username=hub-admin%40example.com")
    req = multi.calls[0].request
    assert req.headers["Authorization"] == "Bearer t"
    sent = json.loads(req.content)
    assert {p["dataset_id"] for p in sent} == set(DATASETS.values())
    assert path.name == f"global-{dt.date.today():%Y-%m-%d}.html"
    assert path.read_text() == "<html>g</html>"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@respx.mock
def test_render_requires_superuser(tmp_path):
    _health()
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "t"}))
    respx.get(f"{BASE}/api/v1/users/me").mock(
        return_value=httpx.Response(200, json={"id": USERS["hub-admin"], "is_superuser": False}))
    multi = respx.post(f"{BASE}/api/v1/visualize/multi")
    with pytest.raises(SystemExit, match="--setup"):
        viz.main(_files(tmp_path) + ["--context", "personal"])
    assert not multi.called
