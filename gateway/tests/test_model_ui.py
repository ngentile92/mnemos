"""v0.9 UI: graph v2 shows only model entities; the model UI script ships and is wired into the page."""
from pathlib import Path

from hub_gateway.local_model import Model
from hub_gateway.local_ui import entity_graph

STATIC = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"


def test_entity_graph_v2(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.me("Ana")
    assert entity_graph(m) is None  # too few entities: the caller falls back to the note graph
    m.add_fact("a", "[[Atlas]] is a billing service.")
    m.add_fact("b", "[[Atlas]] stores invoices in [[Ledgerly]].")
    m.add_fact("c", "[[Ledgerly]] is run by Tomás Iriarte.", about="Ledgerly")
    m.add_fact("d", "Her manager is Tomás Iriarte.")
    m.add_fact("e", "Ana prefers short answers.")
    g = entity_graph(m)
    labels = {n["label"] for n in g["nodes"]}
    assert g["source"] == "entities" and {"Atlas", "Ledgerly", "Tomás Iriarte"} <= labels and "Ana" not in labels
    assert all(n["kind"] == "entity" for n in g["nodes"])
    ids = {n["label"]: n["id"] for n in g["nodes"]}
    pairs = {frozenset((e["source"], e["target"])) for e in g["edges"]}
    assert frozenset((ids["Atlas"], ids["Ledgerly"])) in pairs
    assert any(n["label"] == "Ana" for n in entity_graph(m, show_me=True)["nodes"])


def test_model_ui_wired():
    html = (STATIC / "index.html").read_text()
    assert '<script src="/ui/model.js"></script>' in html and 'id="pf-card"' in html and 'id="mg-card"' in html
    assert html.index('id="pf-card"') < html.index('id="rl-card"')  # profile card comes first
    js = (STATIC / "model.js").read_text()
    for ep in ("model/profile", "model/entities", "model/entity", "model/digest/act", "model/why", "model/not-relevant",
               "model/migrate", "model/migrate/undo", "model/consolidate", "model/consolidate/undo"):
        assert f"/api/{ep}" in js
