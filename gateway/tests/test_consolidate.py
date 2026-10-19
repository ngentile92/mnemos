import asyncio
import datetime as dt

from hub_gateway import local_consolidate as lc
from hub_gateway.local_model import Model

T = dt.date(2026, 10, 1)


def _m(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.me("Ana")
    m.add_fact("a", "Ana lives in Rosario.", recorded_at="2026-01-01")
    m.add_fact("b", "Ana prefers short answers.", recorded_at="2026-01-02")
    m.add_fact("c", "The [[Huerta]] price widget uses Kaiko.", recorded_at="2026-02-01")
    m.add_fact("d", "Switched the [[Huerta]] price widget from Kaiko to Tiingo.", recorded_at="2026-08-01")
    m.add_fact("e", "Ana has 3 cats.", recorded_at="2026-01-03")
    m.add_fact("f", "Ana has 2 cats.", recorded_at="2026-09-03")
    m.add_fact("g", "Travelling to Córdoba next week.", tags=["until-2026-09-10"], recorded_at="2026-09-01")
    m.add_fact("h", "[[Huerta]] is built with SvelteKit and Supabase.", recorded_at="2026-03-01")
    m.add_fact("i", "Her manager is Tomás Iriarte.", recorded_at="2026-03-02")
    return m


def test_consolidate_supersedes_expires_and_profiles(tmp_path):
    m = _m(tmp_path)
    st = asyncio.run(lc.consolidate(m, None, T))
    assert st["expired"] == 1 and st["superseded"] == 2
    c, e = m.fact("c"), m.fact("e")
    assert c["status"] == "superseded" and c["superseded_by"] == "d" and c["valid_to"] == "2026-08-01"
    assert e["status"] == "superseded" and m.fact("f")["status"] == "active"
    assert m.fact("g")["status"] == "expired"
    huerta = next(x for x in m.entities() if x["name"] == "Huerta")
    assert "[1]" in huerta["summary"] and set(huerta["summary_facts"]) <= {"d", "h"}
    prof = lc.profile_text(m)
    assert "## About" in prof and "Rosario" in prof and "## Preferences" in prof and "## People" in prof
    assert "Kaiko." not in prof.split("Working on")[0] and "3 cats" not in prof and "Córdoba" not in prof
    # whole run undoable
    m.undo_job(st["job"])
    assert m.fact("c")["status"] == "active" and m.fact("g")["status"] == "active"


class FakeLLM:
    async def json(self, prompt):
        return {"sentences": [{"text": "Huerta is a garden app on SvelteKit.", "facts": [1]},
                              {"text": "Invented claim.", "facts": [99]}]}


def test_llm_summary_must_cite(tmp_path):
    m = _m(tmp_path)
    asyncio.run(lc.consolidate(m, FakeLLM(), T))
    h = next(x for x in m.entities() if x["name"] == "Huerta")
    assert h["summary"] == "Huerta is a garden app on SvelteKit. [1]" and len(h["summary_facts"]) == 1


def test_decay_and_digest(tmp_path):
    m = _m(tmp_path)
    f = m.fact("a")
    assert lc.strength(f, T) < lc.strength({**f, "use_count": 5, "last_used_at": "2026-09-30"}, T)
    assert lc.strength({**f, "importance": 1.0}, T) == 1.0
    d = lc.digest(m, T)
    assert d and d[0]["note_id"] == "a"
    m.confirm("a")
    assert all(x["note_id"] != "a" for x in lc.digest(m, dt.date.today()))
