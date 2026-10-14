"""Import into local mode: parsers, dedupe, preview → commit through memory_save."""
from __future__ import annotations

import io
import json
import zipfile

from starlette.testclient import TestClient

from hub_gateway import local, local_import as li
from test_local_ui import W, home, unlock  # noqa: F401 — fixture

CONV = [{"title": "Trip", "update_time": 2, "mapping": {
    "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["I live in Lisbon with my wife Marta. Can you plan a trip?"]}}},
    "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["I am happy to help you plan."]}}},
    "c": {"message": {"author": {"role": "user"}, "content": {"parts": ["Do I like trains?"]}}},
}}, {"title": "Food", "update_time": 1, "mapping": {
    "d": {"message": {"author": {"role": "user"}, "content": {"parts": ["Me encanta el asado los domingos."]}}}}}]


def test_chatgpt_user_facts_only():
    facts = li.from_chatgpt(CONV)
    assert any("Lisbon" in f and "Trip" in f for f in facts)
    assert any("asado" in f for f in facts)
    assert not any("happy to help" in f for f in facts) and not any("trains" in f for f in facts)


def test_chatgpt_zip_and_errors():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export/conversations.json", json.dumps(CONV))
    assert li.chatgpt_bytes(buf.getvalue())
    for bad in (b"PK\x03\x04garbage", b"{}", b"hello"):
        try:
            li.chatgpt_bytes(bad)
            raise AssertionError("should fail")
        except (ValueError, zipfile.BadZipFile):
            pass


def test_lines_markdown_and_dedupe():
    assert li.from_lines("- Prefers tea over coffee\n- Works as a nurse\nThings:\n") == ["Prefers tea over coffee", "Works as a nurse"]
    md = li.from_markdown([{"name": "notes/Garden.md", "text": "---\ntags: x\n---\nTomatoes need sun"},
                           {"name": "x.png", "text": "binary"},
                           {"name": "Long.md", "text": "# A\n" + "word " * 300 + "\n# B\n" + "other " * 300}])
    assert md[0] == "Garden: Tomatoes need sun" and sum(m.startswith("Long:") for m in md) >= 2
    p = li.preview(["Works as a nurse", "works as a NURSE!", "Lives in Porto with two cats", "Prefers tea over coffee"],
                   existing=["Prefers tea over coffee."])
    dups = [i["duplicate"] for i in p["items"]]
    assert dups == [None, "repeated in this import", None, "already in your memory"] and p["new"] == 2


def test_preview_commit_end_to_end(home):  # noqa: F811
    with TestClient(local.http_app(home, 8765), base_url="http://127.0.0.1:8765") as c:
        unlock(c, home)
        c.post("/api/memory/save", json={"context": "personal", "text": "Works as a nurse in Porto"}, headers=W)
        r = c.post("/api/import/preview", json={"context": "personal", "source": "claude",
                                                 "text": "- Works as a nurse in Porto\n- Has two cats named Bo and Li"}, headers=W).json()
        assert [i["duplicate"] is None for i in r["items"]] == [False, True]
        up = c.post("/api/import/upload?context=personal", content=json.dumps(CONV).encode(), headers=W).json()
        assert up["new"] >= 2
        done = c.post("/api/import/commit", json={"context": "personal", "source": "claude",
                                                   "notes": ["Has two cats named Bo and Li", "Works as a nurse in Porto"]}, headers=W).json()
        assert done == {"saved": 1, "skipped": 1, "errors": [], "failed": 0}
        items = c.get("/api/memory/list", params={"context": "personal"}).json()["items"]
        assert any("two cats" in i["text"] for i in items)
        assert not any("two cats" in i["text"] for i in c.get("/api/memory/list", params={"context": "work"}).json()["items"])
        assert c.post("/api/import/commit", json={"context": "nope", "source": "claude", "notes": ["x" * 20]}, headers=W).status_code == 400
