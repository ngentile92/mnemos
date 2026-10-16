"""Local nightly cleanup: proposals in the inbox format, actions go through the tools, no Ollama needed."""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastmcp import Client

from hub_gateway import local, local_hygiene as lh
from hub_gateway.local_ui import UI


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


def test_lexical_rules():
    assert lh.lexical_conflict("Ana tiene 2 perros en casa", "Ana tiene 3 perros en casa")
    assert lh.lexical_conflict("Ana come carne los domingos", "Ana ya no come carne los domingos")
    assert not lh.lexical_conflict("Ana come carne", "Ana come carne asada")
    assert lh.merged("Ana vive en Madrid.", "Ana vive en Madrid. Trabaja en Acme.").endswith("Trabaja en Acme.")


async def test_run_and_act(home):
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    async with Client(s["personal"]) as p:
        for t in ["Ana tiene 2 perros llamados Toby y Rex en casa", "Ana tiene 3 perros llamados Toby y Rex en casa",
                  "El proyecto Atlas usa Postgres y corre en Hetzner desde marzo",
                  "El proyecto Atlas usa Postgres y corre en Hetzner desde marzo."]:
            await p.call_tool("memory_save", {"text": t, "allow_duplicate": True})
    rep = await lh.run(home, list(s), ui.call, judge=None)
    kinds = sorted(i["type"] for i in rep["items"])
    assert kinds == ["contradiction", "merge"] and rep["mode"] == "lexical"
    assert (home / "data" / "hygiene.json").stat().st_mode & 0o777 == 0o600
    pend = lh.pending(home)
    assert pend["total"] == 2 and len(pend["items"]) == 2
    merge = next(i for i in pend["items"] if i["type"] == "merge")
    await lh.act(home, {"key": merge["key"], "action": "accept"}, ui.call)
    contra = next(i for i in pend["items"] if i["type"] == "contradiction")
    await lh.act(home, {"key": contra["key"], "action": "keep_newer"}, ui.call)
    assert lh.pending(home)["items"] == []
    items = (await ui.call("personal", "memory_list", {"limit": 50}))["items"]
    assert len(items) == 3 and sum(1 for i in items if i.get("obsolete")) == 1
    # stale: a year later, unpinned notes are proposed
    rep = await lh.run(home, list(s), ui.call, today=dt.date.today() + dt.timedelta(days=400), judge=None)
    assert any(i["type"] == "stale" for i in rep["items"])


async def test_ollama_judge_used_when_available(home):
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    async with Client(s["personal"]) as p:
        await p.call_tool("memory_save", {"text": "Ana trabaja en Acme como ingeniera de datos"})
        await p.call_tool("memory_save", {"text": "Ana trabaja en Globex como ingeniera de datos"})

    async def judge(a, b):
        return "Different employer."
    rep = await lh.run(home, list(s), ui.call, judge=judge)
    assert [i["explain"] for i in rep["items"] if i["type"] == "contradiction"] == ["Different employer."]
