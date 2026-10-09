from fastmcp import Client

from hub_gateway.local_search import LocalIndex, entity_docs, entity_list
from test_server import NOTE_IDS, data, make_server  # noqa: F401 (fixture)


async def _ret(v):
    return v


async def test_entity_docs_by_link_or_phrase(tmp_path):
    idx = LocalIndex(str(tmp_path / "s.sqlite"))
    notes = {"a": "Laura Méndez es CTO de [[Brisa Labs]].", "b": "brisa labs usa PostgreSQL.",
             "c": "Brisa es una palabra.", "d": "Reunión con [[Laura Méndez]] y [[Brisa Labs|Brisa]]."}
    items = [{"id": i, "createdAt": f"2026-10-0{n}"} for n, i in enumerate(notes, 1)]
    await idx.sync("personal", lambda: _ret(items), lambda d: _ret(notes[d]), force=True)
    got = entity_docs(idx, "Brisa Labs", ["personal"])
    assert [g["id"] for g in got] == ["a", "b", "d"] and got[0]["linked"] and not got[1]["linked"]
    assert entity_docs(idx, "Brisa Labs", ["work"]) == []
    assert entity_list(idx, ["personal"]) == [{"entity": "Brisa Labs", "notes": 2}, {"entity": "Laura Méndez", "notes": 1}]


async def test_memory_entity_tool(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        page = data(await c.call_tool("memory_entity", {"name": "Orion", "include_shared": False}))
        listed = data(await c.call_tool("memory_entity", {}))
    assert [n["id"] for n in page["notes"]] == [NOTE_IDS["personal"]] and page["summary"] is None
    assert listed["entities"] == []
