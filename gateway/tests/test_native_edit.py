import asyncio
import json

import pytest

from hub_gateway import local_native as N


class UI:
    def __init__(self, home):
        self.home, self.servers, self.hooks = home, {"personal": object()}, {}

    async def action(self, action, body):
        return {"items": [{"text": "I prefer short answers in every chat"}]}


def _setup(tmp_path):
    scan = tmp_path / "scan"
    (scan / ".claude").mkdir(parents=True)
    f = scan / ".claude" / "CLAUDE.md"
    f.write_text("# Me\n- I prefer short answers\n- I work as a designer at Acme\n")
    nat = N.Native(UI(tmp_path / "home"), home_dir=scan)
    res = asyncio.run(nat.get({}))
    it = next(i for a in res["apps"] for i in a["items"] if i["kind"] == "CLAUDE.md")
    return nat, f, it


def test_compare_marks_known_and_new(tmp_path):
    nat, f, _ = _setup(tmp_path)
    r = asyncio.run(nat.compare({"path": str(f)}))
    assert [x["known"] for x in r["lines"]] == [True, False] and r["known"] == 1 and r["only_here"] == 1


def test_save_needs_confirm_backs_up_and_restores(tmp_path):
    nat, f, it = _setup(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(nat.save({"path": str(f), "text": "x", "modified": it["modified"]}))  # no explicit confirm
    old = f.read_text()
    r = asyncio.run(nat.save({"path": str(f), "text": "# Me\n- new line", "modified": it["modified"], "confirm": True}))
    assert f.read_text() == "# Me\n- new line\n"
    bk = tmp_path / "home" / "data" / "native-backups" / r["backup"]["file"]
    assert bk.read_text() == old  # the previous file was copied before writing
    lst = asyncio.run(nat.backups({"path": str(f)}))["backups"]
    asyncio.run(nat.restore({"path": str(f), "file": lst[0]["file"], "confirm": True}))
    assert f.read_text() == old
    idx = json.loads((tmp_path / "home" / "data" / "native-backups" / "index.json").read_text())
    assert [x["why"] for x in idx[str(f)]] == ["before restore", "before edit"]


def test_save_refuses_stale_unknown_and_mnemos_files(tmp_path):
    nat, f, it = _setup(tmp_path)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(nat.save({"path": str(f), "text": "x", "modified": "2000-01-01T00:00", "confirm": True}))
    with pytest.raises(ValueError):
        asyncio.run(nat.save({"path": str(tmp_path / "other.md"), "text": "x", "confirm": True}))
    m = f.parent / "rules" / "mnemos.md"
    nat.allowed[str(m)] = []
    m.parent.mkdir()
    m.write_text("x")
    with pytest.raises(ValueError):
        asyncio.run(nat.save({"path": str(m), "text": "y", "confirm": True}))


def test_ui_polish_strings():
    from pathlib import Path
    s = Path(N.__file__).parent / "local_ui_static"
    js = (s / "memory.js").read_text()
    assert "<dt>Space</dt>" in js and "spOpts(others)" in js and "/metadata|cognee/i" in js
    assert 'id="me-b-clear">Deselect<' in (s / "index.html").read_text()
    n = (s / "native.js").read_text()
    assert "/api/native/save" in n and 'id="nm-ok"' in n and "confirm: true" in n
