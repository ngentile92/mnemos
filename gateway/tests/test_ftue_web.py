"""FTUE #191, #192, #196, #197, #198, #200: about block, aha demo, cloud block, starter skills, empty states, timing."""

import datetime as dt
import json
from pathlib import Path

from fastmcp import Client

from hub_gateway import local, local_ftue, starter_skills
from hub_gateway.skills import scan

STATIC = Path(local.__file__).parent / "local_ui_static"


def data(r):
    return r.structured_content or json.loads(r.content[0].text)


def test_starter_skills_load_and_never_overwrite(tmp_path):
    assert starter_skills.install(tmp_path) == list(starter_skills.STARTERS)
    skills, errors = scan(tmp_path)
    assert errors == [] and set(starter_skills.STARTERS) <= set(skills)
    assert all(skills[n].summary().get("try") for n in starter_skills.STARTERS)
    f = tmp_path / "skills/shared/email-reply/SKILL.md"
    f.write_text(f.read_text().replace("Email reply", "Mine"))
    assert starter_skills.install(tmp_path) == [] and "Mine" in f.read_text()


async def test_new_memory_knows_name_spaces_and_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "files")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    s = local.build_all(h, 8765)
    async with Client(s["personal"]) as p:
        st = data(await p.call_tool("memory_start", {}))
    about = st["about"]
    assert about["name"] == "Ana" and about["spaces"][:2] == ["personal", "work"]
    assert {"email-reply", "meeting-summary", "weekly-plan"} <= set(about["skills"])


def test_ftue_timing_local_only(tmp_path):
    t0 = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.UTC)
    local_ftue.mark(tmp_path, "welcome", t0)
    local_ftue.mark(tmp_path, "aha", t0 + dt.timedelta(minutes=4, seconds=30))
    local_ftue.mark(tmp_path, "aha", t0 + dt.timedelta(minutes=9))  # first time wins
    assert local_ftue.summary(tmp_path)["minutes"] == {"aha": 4.5}


def test_ui_pieces():
    html = (STATIC / "index.html").read_text()
    for el in ("hm-demo", "hm-demo-skip", "c-block", "s-examples", "ft-out"):
        assert f'id="{el}"' in html
    home = (STATIC / "home.js").read_text()
    assert '"aha"' in home and '"skipped"' in home
    js = (STATIC / "local.js").read_text()
    assert "/api/cloud/block" in js and "s-try" in js and "/api/skills/examples" in js
    assert "Show me the next step" in (STATIC / "apps.js").read_text()
