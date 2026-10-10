"""Dashboard Memory tab (editor phase 1): list/history/update/delete/undo through the gateway's own tools,
via its key-gated internal listener. Cognee is a mock (scratch data); no real memory is touched."""
from __future__ import annotations

import importlib.util
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn

from hub_gateway import app as app_mod
from hub_gateway.audit import Audit
from hub_gateway.config import Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS
from test_server import NOTE_IDS, CogneeRecorder

ROOT = Path(__file__).resolve().parents[2]
KEY = "m" * 40


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def dash(tmp_path, monkeypatch, skills_repo, policy_file):
    monkeypatch.setenv("HUB_CONTEXT", "personal")
    monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "gw"))
    rec = CogneeRecorder()
    mcp = app_mod.build_server(
        Settings.from_env(),
        cognee=CogneeClient("http://c", "k", transport=httpx.MockTransport(lambda r: rec.handler(r))),
        datasets=DatasetMap(DATASETS), skills=SkillIndex(skills_repo, "personal"),
        broker=SecretBroker(load_policy(str(policy_file), "personal"), None), audit=Audit("personal", str(tmp_path / "a")))
    gport = _free_port()
    gw = uvicorn.Server(uvicorn.Config(app_mod._internal_app(mcp, KEY), host="127.0.0.1", port=gport, log_level="warning"))
    threading.Thread(target=gw.run, daemon=True).start()
    for _ in range(100):
        if gw.started:
            break
        time.sleep(0.05)
    (tmp_path / ".env").write_text(f"HUB_INTERNAL_KEY_PERSONAL={KEY}\nMNEMOS_INTERNAL_URL_PERSONAL=http://127.0.0.1:{gport}/mcp\n")
    spec = importlib.util.spec_from_file_location("mnemos_dashboard_mem", ROOT / "dashboard" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(None, set(), None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = mod.make_handler(None, {f"127.0.0.1:{port}"}, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", rec, tmp_path
    srv.shutdown()
    gw.should_exit = True


def post(base, path, body, **hdr):
    return httpx.post(base + path, json=body, timeout=60, headers={"Origin": base, "X-Mnemos-Write": "1", **hdr})


def test_browse_edit_forget_undo(dash):
    base, rec, _ = dash
    nid = NOTE_IDS["personal"]
    j = httpx.get(f"{base}/api/memory/list", params={"context": "personal"}, timeout=60).json()
    by = {i["id"]: i for i in j["items"]}
    assert by[nid]["editable"] is True and by[NOTE_IDS["shared"]]["editable"] is False
    assert NOTE_IDS["work"] not in by  # other contexts never listed

    r = post(base, "/api/memory/update", {"context": "personal", "id": nid, "text": "corrected by dashboard"})
    assert r.status_code == 200, r.text
    assert rec.patches and rec.texts[nid] == "corrected by dashboard"
    h = httpx.get(f"{base}/api/memory/history", params={"context": "personal", "id": nid}, timeout=60).json()
    v = h["previous_versions"][0]
    assert v["action"] == "update" and "orion" in v["text"] and "dashboard" in (v["by"] or "")

    r = post(base, "/api/memory/undo", {"context": "personal", "id": nid})
    assert r.status_code == 200 and "orion" in rec.texts[nid]

    r = post(base, "/api/memory/delete", {"context": "personal", "id": nid})
    assert r.status_code == 200 and rec.deletes[-1][1] == nid
    r = post(base, "/api/memory/undo", {"context": "personal", "id": nid})
    assert r.status_code == 200 and r.json()["action"] in ("delete", "restored", "undelete") or rec.remembers


def test_shared_and_guard(dash):
    base, rec, _ = dash
    r = post(base, "/api/memory/delete", {"context": "personal", "id": NOTE_IDS["shared"]})
    assert r.status_code == 400 and not rec.deletes
    r = httpx.post(f"{base}/api/memory/delete", json={"context": "personal", "id": NOTE_IDS["personal"]})
    assert r.status_code == 403 and not rec.deletes  # no write header/origin
    r = post(base, "/api/memory/update", {"context": "nope", "id": NOTE_IDS["personal"], "text": "xyz"})
    assert r.status_code == 400
    r = post(base, "/api/memory/update", {"context": "personal", "id": "../x", "text": "xyz"})
    assert r.status_code == 400


def test_editor_off_without_key(dash):
    base, _, root = dash
    (root / ".env").write_text("TS_TAILNET=x\n")
    r = httpx.get(f"{base}/api/memory/list", params={"context": "personal"}, timeout=30)
    assert r.status_code == 409 and "HUB_INTERNAL_KEY_PERSONAL" in r.json()["error"]


def test_pin_obsolete_dispute_via_dashboard(dash):
    base, rec, _ = dash
    nid = NOTE_IDS["personal"]
    assert post(base, "/api/memory/pin", {"context": "personal", "id": nid, "pinned": True}).status_code == 200
    assert post(base, "/api/memory/obsolete", {"context": "personal", "id": nid, "reason": "old"}).status_code == 200
    items = {i["id"]: i for i in httpx.get(f"{base}/api/memory/list", params={"context": "personal"}, timeout=60).json()["items"]}
    assert items[nid]["pinned"] and items[nid]["obsolete"]
    r = post(base, "/api/memory/dispute", {"context": "personal", "correction": "orion ya cerró"})
    assert r.status_code == 200 and "proposals" in r.json() and not rec.patches
    assert post(base, "/api/memory/dispute", {"context": "personal", "correction": "x"}).status_code == 400


def test_save_new_note_via_dashboard(dash):
    base, rec, _ = dash
    r = post(base, "/api/memory/save", {"context": "personal", "text": "Quiero cada cambio en su propio PR"})
    assert r.status_code == 200, r.text
    assert rec.remembers and "Quiero cada cambio en su propio PR" in rec.remembers[-1] and "dashboard" in rec.remembers[-1]
    assert post(base, "/api/memory/save", {"context": "personal", "text": "x"}).status_code == 400
    assert httpx.post(f"{base}/api/memory/save", json={"context": "personal", "text": "abcdef"}).status_code == 403
