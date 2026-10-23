from hub_gateway import local_graph as lg


def test_junk_words_dropped():
    for w in ["GLOBAL_PREFERENCES", "english", "idioma español", "aplicación", "la aplicación", "2026-10-23", "x"]:
        assert lg.junk(w), w
    for w in ["Nimbus", "Orion", "Ana Pérez", "Python", "me"]:
        assert not lg.junk(w), w


def test_aliases_merged_by_rule():
    names = {"nimbus": 5, "nimbus project": 2, "nimbus-e": 1, "nimbus.info": 1,
             "ana lopez": 3, "anabel lopez": 1, "ana pérez": 2, "juan pérez": 2}
    m = lg.tidy(names)
    for k in ("nimbus project", "nimbus-e", "nimbus.info"):
        assert m[k] == "nimbus"
    assert m["anabel lopez"] == "ana lopez"
    assert "juan pérez" not in m and "ana pérez" not in m


def test_view_hides_noise_and_merges():
    items = [{"id": "n1", "text": "a"}, {"id": "n2", "text": "b"}]
    t = [("n1", "Nimbus project", "uses", "english"), ("n1", "Nimbus", "built with", "Orion"),
         ("n2", "Nimbus.info", "hosts", "Orion"), ("n2", "GLOBAL_PREFERENCES", "set", "Orion")]
    v = lg.view(items, t, merge=lg.tidy({lg.norm(x): 1 for _n, a, _r, b in t for x in (a, b)}))
    labels = sorted(n["label"] for n in v["nodes"])
    assert labels == ["Nimbus", "Orion"]
    assert v["edges"][0]["weight"] == 2


def test_build_records_recent_and_eta(tmp_path):
    import asyncio

    class L:
        name = "fake"

        async def json(self, prompt):
            return {"triples": [["Orion", "talks to", "Nimbus", 0], ["Orion", "speaks", "english", 0]]}
    g = lg.Graph(tmp_path / "graph.sqlite")
    asyncio.run(g.build([{"id": "n1", "text": "Orion talks to Nimbus"}], L()))
    assert [x[1:] for x in g.recent] == [("Orion", "talks to", "Nimbus")]
    assert g.progress["done"] == 1 and g.progress["started"]
    assert g.drop_junk() == 1
