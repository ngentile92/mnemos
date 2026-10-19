import asyncio
import datetime as dt

import pytest

from hub_gateway import local, local_consolidate as lc
from hub_gateway.local_model import Model
from hub_gateway.local_retrieval import rerank
from hub_gateway.local_ui import UI


def test_rerank_demotes_superseded_and_boosts_entity(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.add_fact("old", "The [[Huerta]] widget uses Kaiko.", recorded_at="2026-01-01")
    m.add_fact("new", "Switched the [[Huerta]] widget from Kaiko to Tiingo.", recorded_at="2026-06-01")
    m.add_fact("x", "Ana likes Kaiko t-shirts.", importance=0.3)
    asyncio.run(lc.consolidate(m, None, dt.date(2026, 7, 1)))
    res = [{"id": "old", "text": "a"}, {"id": "x", "text": "b"}, {"id": "new", "text": "c"}]
    out = rerank(m, "what does Huerta use for prices", res)
    assert [r["id"] for r in out][0] == "new" and out[-1]["id"] == "old" and out[-1]["status"] == "superseded"
    assert out[0]["why"]["about"] == "Huerta"
    hist = rerank(m, "what did Huerta use before", res, touch=False)
    assert next(r for r in hist if r["id"] == "old")["status"] == "superseded"
    assert m.fact("new")["use_count"] == 1


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    home = tmp_path / "mnemos"
    local.init_home(home, ["personal"], "Ana")
    return UI(local.build_all(home, 8765), home, "")


async def test_search_has_why_and_entity_cards(ui):
    for t in ["[[Huerta]] is a community garden app.", "[[Huerta]] runs on SvelteKit and Supabase.",
              "Ana lives in Rosario."]:
        await ui.call("personal", "memory_save", {"text": t})
    m = Model(ui.home / "data" / "personal" / "model.sqlite")
    await lc.consolidate(m, None)
    r = await ui.call("personal", "memory_search", {"query": "Huerta stack", "mode": "keyword"})
    assert r["results"] and r["results"][0]["why"]["about"] == "Huerta"
    assert r["entities"][0]["entity"] == "Huerta" and "[1]" in r["entities"][0]["summary"]
