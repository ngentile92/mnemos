"""Live capture warm lane: quiet sessions → Recently learned (auto) or Inbox (conflict / unsure); undo; profile."""
import time

import pytest

from hub_gateway import local, local_capture as lc, local_spool
from hub_gateway.local_ui import UI


class LLM:
    kind, name = "builtin", "fake"

    def __init__(self, facts):
        self.facts, self.calls = facts, 0

    async def json(self, prompt):
        self.calls += 1
        return {"topic": "Atlas", "facts": self.facts}


class Eng:
    def __init__(self, llm):
        self._l = llm

    def llm(self, _f):
        return self._l


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    home = tmp_path / "mnemos"
    local.init_home(home, ["personal", "work"], "Ana")
    return UI(local.build_all(home, 8765), home, "")


class Personal:
    def get(self, k):
        return {}

    def default_for(self, rec):
        return "personal"


FACTS = [
    {"text": "Ana leads the Atlas billing project at Acme.", "kind": "project_state", "importance": 4, "durability": "long"},
    {"text": "Ana prefers Postgres over MySQL for new services.", "kind": "preference", "importance": 3, "durability": "permanent"},
    {"text": "Ana might try Rust for the Atlas parser.", "kind": "goal", "importance": 3, "durability": "medium"},
    {"text": "Ana has 2 cats.", "kind": "identity", "importance": 4, "durability": "permanent"},
]


async def test_quiet_session_is_learned_and_undoable(hub):
    await hub.call("personal", "memory_save", {"text": "Ana has 3 cats."})
    local_spool.bank(hub.home, "claude_code", "s1", "I lead Atlas billing at Acme and I prefer Postgres over MySQL. Maybe Rust for the parser. We have 2 cats now.")
    cap = lc.Capture(hub, Eng(LLM(FACTS)), Personal())
    assert await cap.run_once() == {"sessions": 0, "learned": 0, "inbox": 0, "skipped": 0}  # not quiet yet
    r = await cap.run_once(now=time.time() + lc.IDLE_S + 1)
    assert r["sessions"] == 1 and r["learned"] == 2 and r["inbox"] == 2
    st = await cap.h_status({})
    assert {x["text"] for x in st["recent"]} == {FACTS[0]["text"], FACTS[1]["text"]}
    inbox = {x["text"]: x for x in st["inbox"]}
    assert inbox["Ana has 2 cats."]["reason"] == "conflict" and inbox["Ana has 2 cats."]["conflict_with"]
    assert inbox[FACTS[2]["text"]]["reason"] == "unsure"
    notes = await hub._existing("personal")
    assert FACTS[0]["text"] in notes
    assert (hub.home / "profile" / "personal.md").read_text().count("Atlas") == 1
    # nothing new → no more model calls
    calls = cap.engine._l.calls
    await cap.run_once(now=time.time() + lc.IDLE_S + 1)
    assert cap.engine._l.calls == calls
    # undo removes the note
    first = next(x for x in st["recent"] if x["text"] == FACTS[0]["text"])
    await cap.h_undo({"id": first["id"]})
    assert FACTS[0]["text"] not in await hub._existing("personal")
    await cap.h_accept({"id": inbox[FACTS[2]["text"]]["id"]})
    assert FACTS[2]["text"] in await hub._existing("personal")


async def test_paused_app_space_is_skipped(hub):
    class Apps:
        def get(self, k):
            return {"paused": True}

        def default_for(self, rec):
            return "work"
    local_spool.bank(hub.home, "cursor", "s", "I lead Atlas billing at Acme and I prefer Postgres over MySQL. Maybe Rust for the parser. We have 2 cats now.")
    cap = lc.Capture(hub, Eng(LLM(FACTS)), Apps())
    r = await cap.run_once(now=time.time() + lc.IDLE_S + 1)
    assert r["learned"] == 0 and cap.engine._l.calls == 0


def test_decide():
    assert lc.decide({"kind": "preference", "importance": 3, "durability": "long"}) == "auto"
    assert lc.decide({"kind": "event", "importance": 5, "durability": "long"}) == "inbox"
    assert lc.decide({"kind": "goal", "importance": 4, "durability": "short"}) == "inbox"


async def test_memory_profile_tool(hub):
    await hub.call("personal", "memory_save", {"text": "Ana prefers short answers.", "tags": ["kind-preference"]})
    r = await hub.call("personal", "memory_profile", {})
    assert "## Preferences" in r["profile"] and "short answers" in r["profile"]


def test_unsure_goes_to_inbox():
    f = {"text": "Ana has two cats.", "kind": "identity", "importance": 5, "durability": "permanent"}
    assert lc.decide(f) == "auto" and lc.decide({**f, "unsure": True}) == "inbox"
