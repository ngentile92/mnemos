"""Graph data (0.12.2): counted from the store, ghosts pruned, dates for the time-lapse, demo memory."""
from __future__ import annotations

import asyncio
import sqlite3

from hub_gateway import local, local_demo, local_graph
from hub_gateway.local_ui import UI, build_graph, rebuild_derived


def test_one_name_per_concept():
    g = build_graph([{"id": "1", "text": "Ana works on Atlas with Lucía"}, {"id": "2", "text": "ATLAS ships in March, says Lucia"},
                     {"id": "3", "text": "Atlas uses dbt"}])
    labels = [n["label"] for n in g["nodes"]]
    assert labels.count("Atlas") == 1 and "ATLAS" not in labels
    assert len([x for x in labels if x.lower().startswith("luc")]) == 1


def test_demo_seed_dates_spaces_and_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_HYGIENE", "0")
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    h = tmp_path / "demo"
    n = local_demo.seed(h)
    assert 140 <= n <= 160 and local_demo.seed(h) == 0      # idempotent
    assert "bridges" in (h / "contexts.yaml").read_text()
    days = set()
    for db in (h / "data" / "notes").glob("*.sqlite"):
        days |= {r[0][:7] for r in sqlite3.connect(db).execute("SELECT created_at FROM data")}
    assert len(days) >= 10                                    # spread over months, for "How it grew"
    servers = local.build_all(h, 0)
    ui = UI(servers, h, "")
    g = asyncio.run(ui.graph("*"))
    assert g["notes"] == n and set(g["notes_by_space"]) == {"personal", "work"}
    assert len(g["nodes"]) >= 30 and all(x.get("first") for x in g["nodes"])
    assert {x["type"] for x in g["nodes"]} >= {"person", "place", "org"}  # coloured without a model
    bridges = [x for x in g["nodes"] if len(x["spaces"]) > 1]
    assert bridges, "people in both spaces become bridges"


def test_ghosts_are_pruned_and_rebuild_backs_up(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    monkeypatch.setenv("MNEMOS_STORE", "notes")
    monkeypatch.setenv("MNEMOS_HYGIENE", "0")
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal"], "Ana")
    servers = local.build_all(h, 0)
    g = local_graph.get(h, "personal")
    g.store([{"id": "gone", "text": "x"}], [("gone", "Ghost", "haunts", "Castle")])
    assert g.triples()
    rep = asyncio.run(rebuild_derived(h, servers))
    assert rep["personal"]["graph_notes_pruned"] == 1 and not g.triples()
    assert any(b.endswith(".bak-" + b.split(".bak-")[1]) for b in rep["personal"]["backups"])
    g.store([{"id": "gone2", "text": "y"}], [("gone2", "Ghost", "haunts", "Castle")])
    asyncio.run(UI(servers, h, "").graph("personal"))         # every view prunes too
    assert not g.triples()


def test_demo_env_never_points_at_real_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEMOS_DEMO", "1")
    monkeypatch.delenv("MNEMOS_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert local.home_dir(None).name == "Mnemos-demo"
