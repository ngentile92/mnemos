"""Phase 3: hygiene inbox (build/act), move between contexts and promote — scratch data only."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from test_memory_editor import dash, post  # noqa: F401  (fixture)
from test_server import NOTE_IDS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))
import hygiene_inbox as hi  # noqa: E402

A, B, C = "a" * 8 + "-0000-4000-8000-" + "0" * 12, "b" * 8 + "-0000-4000-8000-" + "0" * 12, "c" * 8 + "-0000-4000-8000-" + "0" * 12
REPORT = {"date": "2026-10-11", "notes": [
    {"id": A, "dataset": "personal", "text": "Ana vive en Madrid.", "created_at": "2026-01-01"},
    {"id": B, "dataset": "personal", "text": "Ana vive en Madrid con Toby.", "created_at": "2026-05-01"},
    {"id": C, "dataset": "shared", "text": "nota shared", "created_at": "2025-01-01"}],
    "findings": {"merge_proposals": [{"dataset": "personal", "keep": B, "delete": A, "text": "Ana vive en Madrid con Toby."}],
                 "stale": [A, C]},
    "checks": {"contradictions": [{"older": A, "newer": B, "dataset": "personal", "conflict": "dónde vive"}],
               "outdated": []}}


def test_build_skips_shared_and_acts():
    items = hi.build(REPORT, {"personal": "personal"})
    kinds = sorted(i["type"] for i in items)
    assert kinds == ["contradiction", "merge", "stale"]  # shared stale note skipped (not editable)
    calls = []
    call = lambda a, b: calls.append((a, b)) or {"ok": True}  # noqa: E731
    merge = next(i for i in items if i["type"] == "merge")
    hi.act(merge, "accept", call)
    assert calls == [("update", {"context": "personal", "id": B, "text": "Ana vive en Madrid con Toby."}),
                     ("delete", {"context": "personal", "id": A})]
    calls.clear()
    hi.act(next(i for i in items if i["type"] == "contradiction"), "keep_newer", call)
    assert calls[0][0] == "obsolete" and calls[0][1]["id"] == A
    with pytest.raises(ValueError):
        hi.act(merge, "keep_older", call)


def test_inbox_state_hides_done(tmp_path):
    box = hi.Inbox(tmp_path / "s.json")
    items = hi.build(REPORT, {"personal": "personal"})
    box.mark(items[0]["key"], "dismiss")
    assert len(box.pending(items)) == len(items) - 1
    assert (tmp_path / "s.json").stat().st_mode & 0o777 == 0o600


def test_hygiene_endpoints(dash, monkeypatch):  # noqa: F811
    base, _, tmp = dash
    d = tmp / "hyg"
    d.mkdir()
    rep = json.loads(json.dumps(REPORT))
    nid = NOTE_IDS["personal"]
    rep["notes"].append({"id": nid, "dataset": "personal", "text": "nota de personal sobre orion", "created_at": "2024-01-01"})
    rep["findings"] = {"merge_proposals": [], "stale": [nid]}
    rep["checks"] = {}
    (d / "hygiene-2026-10-11.json").write_text(json.dumps(rep))
    monkeypatch.setenv("MNEMOS_HYGIENE_DIR", str(d))
    j = httpx.get(f"{base}/api/hygiene", timeout=30).json()
    assert j["date"] == "2026-10-11" and len(j["items"]) == 1
    k = j["items"][0]["key"]
    assert httpx.post(f"{base}/api/hygiene/act", json={"key": k, "action": "accept"}).status_code == 403  # no Origin
    r = post(base, "/api/hygiene/act", {"key": k, "action": "accept"})
    assert r.status_code == 200, r.text
    assert httpx.get(f"{base}/api/hygiene", timeout=30).json()["items"] == []
    h = httpx.get(f"{base}/api/memory/list", params={"context": "personal"}, timeout=60).json()
    assert next(i for i in h["items"] if i["id"] == nid).get("obsolete")


def test_move_and_promote_need_confirmation(dash):  # noqa: F811
    base, _, _ = dash
    nid = NOTE_IDS["personal"]
    body = {"context": "personal", "id": nid, "to": "work", "text": "nota de personal sobre orion"}
    assert post(base, "/api/memory/move", body).status_code == 400
    assert post(base, "/api/memory/promote", {"context": "personal", "id": nid}).status_code == 400
    # target without a key: save fails first, so the source note is NOT deleted
    r = post(base, "/api/memory/move", {**body, "confirm": True})
    assert r.status_code != 200
    j = httpx.get(f"{base}/api/memory/list", params={"context": "personal"}, timeout=60).json()
    assert any(i["id"] == nid for i in j["items"])
    r = post(base, "/api/memory/promote", {"context": "personal", "id": nid, "confirm": True})
    assert r.status_code == 200, r.text


def test_move_copies_then_forgets(monkeypatch):
    spec = importlib.util.spec_from_file_location("dash_mv", ROOT / "dashboard" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def fake(action, body):
        calls.append((action, body))
        if action == "list":
            return {"items": [{"id": A, "text": "Ana vive en Madrid."}]}
        return {"ok": action}
    monkeypatch.setattr(mod, "memory_action", fake)
    ctxs = list(mod.CONTEXTS)
    src, dst = ctxs[0], ctxs[1]
    out = mod.memory_move({"context": src, "to": dst, "id": A, "text": "Ana vive en Madrid.", "confirm": True})
    assert [c[0] for c in calls] == ["list", "save", "delete"]
    assert calls[1][1]["context"] == dst and calls[1][1]["tags"] == [f"moved-from-{src}"]
    assert calls[2][1] == {"context": src, "id": A} and out["to"] == dst
    calls.clear()
    with pytest.raises(ValueError):
        mod.memory_move({"context": src, "to": dst, "id": A, "text": "changed text", "confirm": True})
    assert [c[0] for c in calls] == ["list"]
