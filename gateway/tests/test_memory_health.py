"""Memory health: generic checks on existing notes, bulk accept with a backup, batch undo, capture guard.
Synthetic data only."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from hub_gateway import local, local_health as hl, local_hygiene as lh, memfilter as mf
from hub_gateway.local_ui import UI


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Sam Rivera")
    return h


def test_rule_checks():
    notes = [{"id": "1", "text": "def load():\n    return cfg;\nx = {}"},
             {"id": "2", "text": "| col | type |\n|---|---|\n| id | int |"},
             {"id": "3", "text": "The user prefers short answers with examples"},
             {"id": "4", "text": "Sam utiliza Linux en casa"},
             {"id": "5", "text": "Lives in Lisbon with two cats"},
             {"id": "6", "text": "Necesita ayuda con cosas."}]
    items, rest = hl.check_notes("personal", notes, "Sam Rivera")
    by = {i["notes"][0]["id"]: i for i in items}
    assert by["1"]["type"] == by["2"]["type"] == by["6"]["type"] == "junk"
    assert by["3"]["proposed_text"] == "Prefers short answers with examples"
    assert by["4"]["proposed_text"] == "Utiliza Linux en casa"
    assert "5" not in by and {n["id"] for n in rest} == {"3", "4", "5"}


def test_pasted_block():
    assert mf.pasted_block("```\nprint(1)\n```") == "code"
    assert mf.pasted_block("| a | b |\n| 1 | 2 |") == "table"
    assert mf.pasted_block("2026-01-01 10:00 ERROR boom\n2026-01-01 10:01 INFO ok\nmore") == "log"
    assert mf.pasted_block("Prefers tea.\nLives in Lisbon.") is None


async def test_moves_by_rules():
    spaces = [{"name": "personal", "label": "Personal", "description": "family, friends, hobbies, health, home"},
              {"name": "work", "label": "Work", "description": "job, clients, meetings, colleagues, deadlines"}]
    notes = [{"id": "a", "text": "Weekly meetings with clients and colleagues before deadlines"},
             {"id": "b", "text": "Likes the colour green"}]
    got = await hl.check_moves("personal", notes, spaces)
    assert [i["notes"][0]["id"] for i in got] == ["a"] and got[0]["to"] == "work"
    assert await hl.check_moves("personal", notes, spaces[:1]) == []  # one space: nothing to move


async def test_wrong_needs_model_and_uses_it():
    assert await hl.check_wrong("personal", [{"id": "x", "text": "The character is a wizard"}], "Sam", None) == []

    class Fake:
        async def json(self, prompt):
            return {"wrong": "wizard" in prompt, "why": "role-play"}
    got = await hl.check_wrong("personal", [{"id": "x", "text": "The character is a wizard"},
                                            {"id": "y", "text": "Lives in Lisbon"}], "Sam", Fake())
    assert [i["notes"][0]["id"] for i in got] == ["x"]


async def test_capture_guard_rejects_blocks_and_rewrites(home):
    s = local.build_all(home, 8765)
    async with Client(s["personal"]) as p:
        r = (await p.call_tool("memory_save", {"text": "| a | b |\n|---|---|\n| 1 | 2 |"})).structured_content
        assert r["status"] == "rejected" and r["saved_to"] is None
        r = (await p.call_tool("memory_save", {"text": "Sam utiliza Linux en casa"})).structured_content
        assert r["saved_text"] == "Utiliza Linux en casa"
        items = (await p.call_tool("memory_list", {})).structured_content["items"]
        assert [i["text"] for i in items] == ["Utiliza Linux en casa"]


async def test_bulk_accept_backup_and_undo(home):
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    notes = ["The user prefers dark mode in every editor", "The user likes long walks on Sundays",
             "Lives in Lisbon with two cats"]
    async with Client(s["personal"]) as p:
        for t in notes:
            await p.call_tool("memory_save", {"text": t})
    # notes saved before the guard existed: put third-person text back in place directly
    for it in (await ui.call("personal", "memory_list", {"limit": 50}))["items"]:
        if it["text"].startswith(("Prefers", "Likes")):
            await ui.call("personal", "memory_update", {"id": it["id"], "text": "The user " + it["text"][0].lower() + it["text"][1:]})
    rep = await lh.run(home, list(s), ui.call, judge=None, embedder=None)
    pend = lh.pending(home)
    assert pend["counts"].get("rewrite") == 2 and rep["why"] == "schedule"
    r = await lh.act(home, {"type": "rewrite", "action": "accept"}, ui.call, move=ui.move)
    assert r["done"] == 2 and r["failed"] == 0
    bk = list((home / "data" / "backups").glob("health-*"))
    assert len(bk) == 1 and list(bk[0].rglob("*.sqlite"))
    texts = sorted(i["text"] for i in (await ui.call("personal", "memory_list", {"limit": 50}))["items"])
    assert texts == ["Likes long walks on Sundays", "Lives in Lisbon with two cats", "Prefers dark mode in every editor"]
    assert lh.pending(home)["last_batch"]["count"] == 2
    u = await lh.undo_batch(home, r["batch"], ui.call)
    assert u["reverted"] == 2
    texts = sorted(i["text"] for i in (await ui.call("personal", "memory_list", {"limit": 50}))["items"])
    assert texts[1:] == ["The user likes long walks on Sundays", "The user prefers dark mode in every editor"]
    assert lh.pending(home)["counts"].get("rewrite") == 2 and lh.pending(home)["last_batch"] is None
    with pytest.raises(ValueError):
        await lh.undo_batch(home, r["batch"], ui.call)


async def test_junk_forget_and_move_then_undo(home):
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    async with Client(s["personal"]) as p:
        await p.call_tool("memory_save", {"text": "Run npm run build before every git push to main"})
        await p.call_tool("memory_save", {"text": "Lives in Lisbon with two cats"})
    await lh.run(home, list(s), ui.call, judge=None, embedder=None,
                 spaces=[{"name": "personal", "description": "home, cats, family"},
                         {"name": "work", "description": "build, deploy, push, code, main, release"}])
    junk = next(i for i in lh.pending(home)["items"] if i["type"] == "junk")
    r = await lh.act(home, {"key": junk["key"], "action": "accept"}, ui.call, move=ui.move)
    assert r["done"] == 1
    left = [i["text"] for i in (await ui.call("personal", "memory_list", {"limit": 50}))["items"]]
    assert left == ["Lives in Lisbon with two cats"]
    await lh.undo_batch(home, r["batch"], ui.call)
    assert len((await ui.call("personal", "memory_list", {"limit": 50}))["items"]) == 2


async def test_move_accept(home):
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    async with Client(s["personal"]) as p:
        await p.call_tool("memory_save", {"text": "Weekly meetings with clients and colleagues before deadlines"})
    sp = [{"name": "personal", "description": "family, friends, hobbies, home"},
          {"name": "work", "description": "job, clients, meetings, colleagues, deadlines"}]
    await lh.run(home, list(s), ui.call, judge=None, embedder=None, spaces=sp)
    mv = next(i for i in lh.pending(home)["items"] if i["type"] == "move")
    r = await lh.act(home, {"key": mv["key"], "action": "accept"}, ui.call, move=ui.move)
    assert not (await ui.call("personal", "memory_list", {}))["items"]
    assert (await ui.call("work", "memory_list", {}))["items"]
    await lh.undo_batch(home, r["batch"], ui.call)
    assert (await ui.call("personal", "memory_list", {}))["items"] and not (await ui.call("work", "memory_list", {}))["items"]


async def test_import_kicks_a_check(home):
    import asyncio
    from hub_gateway.local_discover import Discover
    s = local.build_all(home, 8765)
    ui = UI(s, home, "")
    ui.after_import = lambda: lh.kick(home, list(s), ui.call, why="import")
    r = await Discover(ui).commit({"notes": [{"text": "The user prefers tea over coffee", "context": "personal"}]})
    assert r["saved"] == 1
    for _ in range(100):
        if not lh.running() and (home / "data" / "hygiene.json").exists():
            break
        await asyncio.sleep(0.05)
    rep = json.loads((home / "data" / "hygiene.json").read_text())
    assert rep["why"] == "import"
