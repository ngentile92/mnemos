"""Dashboard 'Add a context': runs mnemos_context.py add on files only; preview writes nothing; guarded POST."""
from __future__ import annotations

import importlib.util
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dash(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    shutil.copy(ROOT / "config" / "contexts.example.yaml", tmp_path / "config" / "contexts.example.yaml")
    (tmp_path / ".env").write_text("GITHUB_USER=alex\nTS_TAILNET=tail123\nSKILLS_DIR=./skills-local\n")
    spec = importlib.util.spec_from_file_location("mnemos_dashboard_c", ROOT / "dashboard" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(None, set(), None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = mod.make_handler(None, {f"127.0.0.1:{port}"}, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, tmp_path
    srv.shutdown()


def _post(port, body, **hdr):
    h = {"Origin": f"http://127.0.0.1:{port}", "X-Mnemos-Write": "1", **hdr}
    return httpx.post(f"http://127.0.0.1:{port}/api/contexts/add", json=body, headers=h, timeout=60)


def test_preview_then_create(dash):
    port, root = dash
    env_before = (root / ".env").read_text()
    r = _post(port, {"name": "research", "description": "papers", "projects": ["a", "b"]})  # dry_run default
    assert r.status_code == 200 and r.json()["dry_run"] and "dry run" in r.json()["output"]
    assert (root / ".env").read_text() == env_before
    r = _post(port, {"name": "research", "projects": ["a"], "dry_run": False})
    j = r.json()
    assert r.status_code == 200, j
    assert "research" in (root / "config" / "contexts.yaml").read_text()
    assert "GH_OAUTH_RESEARCH_ID" in (root / ".env").read_text()
    assert "hub-research.tail123.ts.net" in j["output"] and "skills-local/skills/research" in j["output"]
    assert (root / "compose.generated.yaml").exists()
    for line in (root / ".env").read_text().splitlines():  # generated values never echoed back
        k, _, v = line.partition("=")
        v = v.split("#")[0].strip()
        if any(w in k for w in ("KEY", "SECRET", "PW")) and len(v) >= 16 and not v.startswith("__"):
            assert v not in j["output"]


@pytest.mark.parametrize("body", [{"name": "Bad Name"}, {"name": "ok", "projects": ["../x"]}, {"name": "work", "dry_run": False}])
def test_rejects(dash, body):
    port, _ = dash
    assert _post(port, body).status_code == 400


def test_guard(dash):
    port, root = dash
    assert _post(port, {"name": "x1", "dry_run": False}, Origin="https://evil.example").status_code == 403
    assert not (root / "config" / "contexts.yaml").exists()
