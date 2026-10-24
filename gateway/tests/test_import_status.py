import os
import time
from pathlib import Path

from hub_gateway import local_imports as L

ROOT = Path(__file__).resolve().parents[1] / "src" / "hub_gateway"


def _src(tmp, name="a.md"):
    f = tmp / name
    f.write_text("I work on things")
    return {"id": "s1", "label": "Tool notes", "kind": "claude", "paths": [str(f)]}, f


def test_new_then_imported_then_changed(tmp_path):
    home = tmp_path / "home"
    s, f = _src(tmp_path)
    assert L.annotate(home, [s])[0]["status"] == "new"
    L.record(home, [s], {"s1": 3}, {"s1": 1}, "b1")
    a = L.annotate(home, [s])[0]
    assert a["status"] == "imported" and a["saved"] == 3 and a["last_import"]
    assert L.summary([a])["up_to_date"] is True
    t = time.time() + 10
    os.utime(f, (t, t))
    b = L.annotate(home, [{**s, "paths": [str(f), str(_src(tmp_path, "b.md")[1])]}])[0]
    assert b["status"] == "changed" and b["new_files"] == 2
    assert L.summary([b]) == {"up_to_date": False, "todo": 1, "imported": 0, "saved": 3}


def test_history_and_undo(tmp_path):
    s, _ = _src(tmp_path)
    L.record(tmp_path, [s], {"s1": 2}, {}, "b9")
    L.mark_undone(tmp_path, "b9", 2)
    h = L.load(tmp_path)["history"][0]
    assert h["undone"] and h["removed"] == 2 and h["sources"] == ["Tool notes"]
    assert "I work on things" not in (tmp_path / "data" / "imports.json").read_text()  # never stores contents


def test_ui_wired():
    js = (ROOT / "local_ui_static" / "local.js").read_text()
    html = (ROOT / "local_ui_static" / "index.html").read_text()
    css = (ROOT / "local_ui_static" / "local.css").read_text()
    assert "/api/discover/history" in js and "source_id: it.source_id" in js and "imp-group" in js
    assert 'id="d-status"' in html and 'id="d-hist"' in html
    assert ".hm-stat b{color:var(--txt)}" in css
    d = (ROOT / "local_discover.py").read_text()
    assert '("GET", "discover/history")' in d and "local_imports.record" in d
