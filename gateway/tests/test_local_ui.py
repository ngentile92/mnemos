"""Local app ("Open my memory"): unlock, notes CRUD through the hub tools, graph, skills, isolation, CSRF/host guards."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hub_gateway import local
from hub_gateway.local_ui import build_graph, entities

W = {"X-Mnemos-Write": "1"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    local.set_password(h, "correct horse battery")
    return h


@pytest.fixture
def client(home):
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        yield c


def unlock(c, home):
    r = c.post("/api/login", json={"token": (home / "token").read_text().strip()}, headers=W)
    assert r.status_code == 200 and "mnemos_ui" in r.cookies


def test_locked_until_token_or_password(client, home):
    assert client.get("/app").status_code == 200 and "Open my memory" in client.get("/").text
    assert client.get("/ui/memory.js").status_code == 200
    assert client.get("/ui/%2e%2e/local.py").status_code in (401, 404)
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/login", json={"password": "nope"}, headers=W).status_code == 401
    assert client.post("/api/login", json={"password": "correct horse battery"}).status_code == 403  # no header
    assert client.post("/api/login", json={"password": "correct horse battery"}, headers=W).status_code == 200
    assert {"personal", "work"} <= set(client.get("/api/session").json()["contexts"])


def test_other_host_rejected(home):
    with TestClient(local.http_app(home, 8765), base_url="http://evil.example") as c:
        assert c.get("/app").status_code == 403


def test_notes_crud_undo_and_isolation(client, home):
    unlock(client, home)
    assert client.post("/api/memory/save", json={"context": "personal", "text": "Ana loves green tea"}).status_code == 403
    r = client.post("/api/memory/save", json={"context": "personal", "text": "Ana loves green tea in Lisbon"}, headers=W)
    assert r.status_code == 200, r.text
    items = client.get("/api/memory/list", params={"context": "personal", "include_shared": "1"}).json()["items"]
    nid = next(i["id"] for i in items if "green tea" in i["text"])
    assert not any("green tea" in i["text"] for i in client.get("/api/memory/list", params={"context": "work"}).json()["items"])
    assert client.post("/api/memory/update", json={"context": "personal", "id": nid, "text": "Ana loves black tea in Lisbon"}, headers=W).status_code == 200
    assert client.post("/api/memory/pin", json={"context": "personal", "id": nid, "pinned": True}, headers=W).status_code == 200
    assert client.post("/api/memory/obsolete", json={"context": "personal", "id": nid, "obsolete": True}, headers=W).status_code == 200
    h = client.get("/api/memory/history", params={"context": "personal", "id": nid}).json()
    assert any("green tea" in v["text"] for v in h["previous_versions"])
    assert client.post("/api/memory/delete", json={"context": "work", "id": nid}, headers=W).status_code == 400  # other context
    assert client.post("/api/memory/delete", json={"context": "personal", "id": nid}, headers=W).status_code == 200
    u = client.post("/api/memory/undo", json={"context": "personal", "id": nid}, headers=W)
    assert u.status_code == 200
    assert u.json()["action"] == "restored"
    assert any("black tea" in i["text"] for i in client.get("/api/memory/list", params={"context": "personal"}).json()["items"])
    g = client.get("/api/graph", params={"context": "personal"}).json()
    assert g["notes"] >= 0 and "nodes" in g


def test_skills_create_list_read(client, home):
    unlock(client, home)
    r = client.post("/api/skills/save", json={"context": "personal", "name": "email-style", "description": "When writing emails",
                                              "body": "Short, warm, no jargon."}, headers=W)
    assert r.status_code == 200, r.text
    assert client.post("/api/skills/save", json={"context": "personal", "name": "../x", "description": "bad", "body": ""},
                       headers=W).status_code == 400
    for _ in range(30):
        sk = client.get("/api/skills", params={"context": "personal"}).json()["skills"]
        if sk:
            break
        time.sleep(0.25)
    assert sk[0]["name"] == "email-style" and sk[0]["editable"]
    assert "no jargon" in client.get("/api/skill", params={"context": "personal", "name": "email-style"}).json()["text"]
    assert client.get("/api/skills", params={"context": "work"}).json()["skills"] == []  # isolated


def test_entity_graph():
    assert {"Ana García", "Lisbon", "#travel"} <= entities("Ana García moved to Lisbon in May. #travel")
    assert "Ana" in entities("Ana's daughter plays") and "Ana's" not in entities("Ana's daughter plays")
    items = [{"id": "1", "text": "Ana works with Pedro on Project Atlas."}, {"id": "2", "text": "Pedro and Ana met in Madrid."},
             {"id": "3", "text": "Old", "obsolete": True}]
    g = build_graph(items)
    labels = {n["label"] for n in g["nodes"]}
    assert {"Ana", "Pedro"} <= labels
    assert any({e["source"], e["target"]} == {"Ana", "Pedro"} and e["weight"] == 2 for e in g["edges"])


def test_memory_js_in_sync_with_dashboard():
    root = Path(__file__).resolve().parents[2]
    a = root / "dashboard" / "static" / "memory.js"
    b = root / "gateway" / "src" / "hub_gateway" / "local_ui_static" / "memory.js"
    if a.exists():
        assert a.read_text() == b.read_text(), "copy dashboard/static/memory.js into local_ui_static/"
