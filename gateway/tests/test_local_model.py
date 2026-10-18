"""Layered memory model: add-only facts linked to entities; migration dry run / apply / exact undo."""
import pytest

from hub_gateway import local
from hub_gateway.local_model import Model, guess_kind
from hub_gateway.local_ui import UI


def test_entity_linking(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.me("Ana")
    a = m.add_fact("n1", "Ana is building [[Atlas]], a billing service.")
    b = m.add_fact("n2", "Atlas uses Postgres and FastAPI.")
    c = m.add_fact("n3", "Ana prefers short answers.")
    d = m.add_fact("n4", "Her manager is Tomás Iriarte.")
    e = m.add_fact("n5", "Decided to deploy the service weekly.", about="Atlas")
    assert a["entity_id"] == b["entity_id"] == e["entity_id"] != "me"
    assert c["entity_id"] == "me" and c["kind"] == "preference"
    ents = {x["name"]: x for x in m.entities()}
    assert ents["Atlas"]["n"] == 3 and "Tomás Iriarte" in ents and ents["Tomás Iriarte"]["kind"] == "person"
    assert e["kind"] == "decision"


def test_add_only_and_set_fact_undo(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.add_fact("n1", "Uses Kaiko for prices.")
    m.add_fact("n1", "something else")  # same note id: ignored, never overwritten
    assert m.fact("n1")["text"] == "Uses Kaiko for prices."
    m.set_fact("n1", "job1", status="superseded", valid_to="2026-01-01")
    assert m.fact("n1")["status"] == "superseded"
    assert m.undo_job("job1") == 2 and m.fact("n1")["status"] == "active" and m.fact("n1")["valid_to"] is None


def test_kind_guess():
    assert guess_kind("x", ["kind-goal"]) == "goal"
    assert guess_kind("Prefiere respuestas cortas") == "preference"
    assert guess_kind("Switched from Kaiko to Tiingo") == "decision"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    home = tmp_path / "mnemos"
    local.init_home(home, ["personal", "work"], "Ana")
    return home


async def test_save_indexes_fact_and_delete_forgets(hub):
    ui = UI(local.build_all(hub, 8765), hub, "")
    r = await ui.call("personal", "memory_save", {"text": "Ana is building [[Huerta]] with SvelteKit."})
    m = Model(hub / "data" / "personal" / "model.sqlite")
    f = m.fact(r["id"])
    assert f and f["entity"] == "Huerta" and f["origin"] == "user_said"
    await ui.call("personal", "memory_delete", {"id": r["id"]})
    assert m.fact(r["id"]) is None


async def test_migration_dry_run_apply_undo(hub):
    servers = local.build_all(hub, 8765)
    ui = UI(servers, hub, "")
    for t in ["Ana lives in Rosario.", "Ana works on [[Atlas]] at Acme.", "Atlas uses Postgres."]:
        await ui.call("personal", "memory_save", {"text": t})
    m = Model(hub / "data" / "personal" / "model.sqlite")
    with m.db() as c:  # pretend these were saved before 0.9 (no model rows yet)
        c.execute("DELETE FROM facts")
    dry = m.migrate(await ui.graph_items("personal"), apply=False)
    assert dry["dry_run"] and dry["notes"] == 3 and any(e["name"] == "Atlas" and e["facts"] == 2 for e in dry["entities"])
    assert m.facts() == []  # dry run wrote nothing
    res = m.migrate(await ui.graph_items("personal"), apply=True)
    assert res["migrated"] == 3 and len(m.facts()) == 3
    assert m.migrate(await ui.graph_items("personal"), apply=False)["notes"] == 0
    assert m.undo_migration(res["batch"])["facts"] == 3 and m.facts() == []
    assert len(await ui._existing("personal")) == 3  # notes untouched
