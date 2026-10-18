"""Graph fixes (0.6): every note (pinned/hand-written first), 'me' collapsed, alias merge, types, directed labelled
edges, clusters, All spaces."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from hub_gateway import local
from hub_gateway import local_graph as lg
from hub_gateway.local_ui import UI

ITEMS = [{"id": "a", "text": "I work at Acme on Atlas", "space": "work"},
         {"id": "b", "text": "Atlas uses Postgres", "space": "work"},
         {"id": "c", "text": "My sister Lia lives in Rome", "space": "personal"},
         {"id": "d", "text": "Lia visits Acme", "space": "personal"}]
TRIPLES = [("a", "me", "works at", "Acme"), ("a", "Me", "works on", "Atlas"), ("b", "Atlas", "uses", "Postgres"),
           ("b", "atlas project", "runs on", "Postgres"), ("c", "Lia", "lives in", "Rome"), ("c", "me", "sister", "Lia"),
           ("d", "Lia", "visits", "Acme")]


def test_view_collapses_me_and_types_direction_clusters():
    v = lg.view(ITEMS, TRIPLES, types={"acme": "org", "lia": "person"}, merge={"atlas project": "atlas"})
    ids = {n["id"] for n in v["nodes"]}
    assert "me" not in ids and {"acme", "atlas", "postgres", "lia", "rome"} <= ids and "atlas project" not in ids
    acme = next(n for n in v["nodes"] if n["id"] == "acme")
    assert acme["type"] == "org" and acme["spaces"] == ["personal", "work"]  # cross-space entity
    e = next(e for e in v["edges"] if e["source"] == "atlas" and e["target"] == "postgres")
    assert e["weight"] == 2 and e["label"] in ("uses", "runs on") and e["relations"][0].startswith("Atlas → ")
    assert all(n["cluster"] >= 0 for n in v["nodes"])
    assert {n["cluster"] for n in v["nodes"] if n["id"] in ("atlas", "postgres")} == {next(n["cluster"] for n in v["nodes"] if n["id"] == "atlas")}
    shown = lg.view(ITEMS, TRIPLES, me=lg.me_names("Ana Pérez"), show_me=True)
    me = next(n for n in shown["nodes"] if n["id"] == "me")
    assert me["label"] == "Me" and me["kind"] == "me" and shown["nodes"][0]["id"] == "me"


class Emb:
    async def embed_sim(self, texts):
        base = {"mnemos": [1, 0, 0], "mnemos app": [0.99, 0.05, 0], "acme": [0, 1, 0], "acme inc": [0.02, 0.99, 0.1]}
        return [base.get(t, [0, 0, 1]) for t in texts]


async def test_aliases_loose_and_embeddings():
    names = {"mnemos": 9, "mnemos-hub": 2, "mnemos hub": 1, "mnemos app": 3, "acme": 5, "acme inc": 2}
    m = await lg.aliases(names, {}, None)
    assert m == {"mnemos hub": "mnemos-hub"} or m == {"mnemos-hub": "mnemos hub"}
    m = await lg.aliases(names, {"acme": "org", "acme inc": "place"}, Emb())
    assert m.get("mnemos app") == "mnemos" and "acme inc" not in m  # different types: kept apart
    m = await lg.aliases(names, {"acme": "org"}, Emb())
    assert m.get("acme inc") == "acme"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    return h


async def test_graph_reads_every_note_ranked_and_all_spaces(home, monkeypatch):
    from hub_gateway import contexts
    monkeypatch.setattr(contexts, "OWNER", "Ana")
    servers = local.build_all(home, 8765)
    first, other = list(servers)[0], list(servers)[1]
    st = servers[first].mnemos_local["store"]
    ds = servers[first].mnemos_local["scope"].write_id(target="context", project=None)[1]
    for i in range(520):  # more than memory_list's 500
        await st.remember(f"Imported fact number {i} about Kafka", ds, metadata={"tags": ["import-claude"]})
    async with Client(servers[first]) as c:
        await c.call_tool("memory_save", {"text": "Ana trabaja con [[Lucía]] en el proyecto Atlas"})
        items = (await c.call_tool("memory_list", {"contains": "Lucía"})).structured_content["items"]
        await c.call_tool("memory_pin", {"id": items[0]["id"]})
    async with Client(servers[other]) as c:
        await c.call_tool("memory_save", {"text": "El proyecto Atlas se entrega en marzo, dijo Ana a Lucía"})
    ui = UI(servers, home, "")
    its = await ui.graph_items(first)
    assert len(its) == 521 and its[0]["pinned"] and its[0]["manual"] and not its[1]["manual"]
    g = await ui.graph("*")
    assert g["spaces"] == list(servers) and g["source"] == "names"
    atlas = next((n for n in g["nodes"] if n["label"] == "Atlas"), None)
    assert atlas and set(atlas["spaces"]) == {first, other}
    assert not any(n["label"] == "Ana" for n in g["nodes"])  # the owner is collapsed unless "show me"
    assert any(n["label"] == "Ana" for n in (await ui.graph("*", show_me=True))["nodes"])
    with pytest.raises(ValueError):
        await ui.graph("nope")
    json.dumps(g)
