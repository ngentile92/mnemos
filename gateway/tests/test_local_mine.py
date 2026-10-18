"""Full-history mining: reads every batch, resumes after a stop, only reads new batches next time, saves only what
was approved, never mines unscanned paths."""
from __future__ import annotations

import json

import pytest

from hub_gateway import local, local_discover as ld, local_mine as lm
from hub_gateway.local_ui import UI


class CountingLLM:
    kind, name = "builtin", "Built-in · fake"

    def __init__(self):
        self.calls = 0

    async def json(self, prompt):
        self.calls += 1
        w = ["Ana", "lives", "in", "Lisbon", "works", "at", "Acme", "likes", "tea", "plays", "chess", "owns", "a", "dog"]
        return {"facts": [" ".join(w[(self.calls * 3 + k) % len(w)] + str(self.calls) for k in range(5)) + "."]}


class FakeEngine:
    def __init__(self, llm):
        self._l = llm

    def llm(self, _f):
        return self._l

    def embedder(self):
        return None


def chats(home, n_files, per_file=12):
    proj = home / ".claude" / "projects" / "-Users-ana-code-atlas"
    proj.mkdir(parents=True, exist_ok=True)
    for f in range(n_files):
        lines = [{"type": "user", "message": {"role": "user", "content": f"file {f} message {i} " + "x" * 900}}
                 for i in range(per_file)]
        (proj / f"s{f}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_OLLAMA", "0")
    monkeypatch.setenv("MNEMOS_BUILTIN", "0")
    home = tmp_path / "mnemos"
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    local.init_home(home, ["personal", "work"], "Ana")
    return UI(local.build_all(home, 8765), home, "")


async def _mine(hub, h, llm, start=True):
    d = ld.Discover(hub, h, engine=FakeEngine(llm))
    m = lm.Miner(hub, d)
    found = (await d.scan({}))["sources"]
    src = next(s for s in found if s["kind"] == "claude_code")
    if start:
        await m.h_start({"sources": [{"id": src["id"], "context": "work"}]})
    else:
        m.st["state"] = "running"
        m.resume_on_start()
    await m.task
    return m, d


def test_batches_are_stable():
    msgs = [f"message {i} " + "y" * 900 for i in range(20)]
    b = lm.batches(msgs)
    assert len(b) == 4 and lm.batches(msgs + ["one more message here"])[:3] == b[:3]
    assert lm.batches(["password=hunter2hunter"]) == []


async def test_mines_everything_then_only_new(tmp_path, hub):
    h = tmp_path / "home"
    chats(h, 3)
    llm = CountingLLM()
    m, d = await _mine(hub, h, llm)
    st = await m.h_status({})
    assert st["state"] == "done" and llm.calls == st["total"] == 6 and st["found"] == 6  # 3 chats x 2 windows
    calls = llm.calls
    m2, _ = await _mine(hub, h, llm)  # nothing new: no model calls
    assert llm.calls == calls and (await m2.h_status({}))["done"] == 6
    chats(h, 4)  # one new chat file
    m3, _ = await _mine(hub, h, llm)
    assert llm.calls == calls + 2


async def test_pause_and_resume_after_restart(tmp_path, hub):
    h = tmp_path / "home"
    chats(h, 2)

    class Pausing(CountingLLM):
        async def json(self, prompt):
            r = await super().json(prompt)
            if self.calls == 2:
                miner.st["state"] = "paused"
            return r
    llm = Pausing()
    d = ld.Discover(hub, h, engine=FakeEngine(llm))
    miner = lm.Miner(hub, d)
    src = next(s for s in (await d.scan({}))["sources"] if s["kind"] == "claude_code")
    await miner.h_start({"sources": [{"id": src["id"], "context": "work"}]})
    await miner.task
    assert miner.st["state"] == "paused" and len(miner.st["seen"]) == 2
    # app restarts while it was running: the state file says so, the new hub resumes
    s = json.loads((hub.home / "mining.json").read_text())
    s["state"] = "running"
    (hub.home / "mining.json").write_text(json.dumps(s))
    again = lm.Miner(hub, ld.Discover(hub, h, engine=FakeEngine(CountingLLM())))
    again.resume_on_start()
    await again.task
    assert again.st["state"] == "done" and len(again.st["seen"]) == 4


async def test_save_and_dismiss(tmp_path, hub):
    h = tmp_path / "home"
    chats(h, 2)
    m, d = await _mine(hub, h, CountingLLM())
    new = (await m.h_status({}))["new"]
    r = await m.h_save({"ids": [new[0]["id"]]})
    assert r["saved"] == 1
    await m.h_dismiss({"ids": [new[1]["id"]]})
    st = await m.h_status({})
    assert st["saved"] == 1 and st["dismissed"] == 1 and len(st["new"]) == 2
    assert new[0]["text"] in await hub._existing("work")


async def test_needs_a_scanned_chat_source_and_a_model(tmp_path, hub):
    h = tmp_path / "home"
    chats(h, 1)
    d = ld.Discover(hub, h, engine=FakeEngine(None))
    m = lm.Miner(hub, d)
    with pytest.raises(ValueError):
        await m.h_start({"sources": [{"id": "nope", "context": "work"}]})
    src = next(s for s in (await d.scan({}))["sources"] if s["kind"] == "claude_code")
    await m.h_start({"sources": [{"id": src["id"], "context": "work"}]})
    await m.task
    assert m.st["state"] == "error" and "needs a model" in m.st["error"]


class TypedLLM:
    kind, name = "builtin", "Built-in · fake"

    def __init__(self):
        self.prompts = []

    async def json(self, prompt):
        self.prompts.append(prompt)
        return {"topic": "Atlas billing service", "facts": [
            {"text": "Ana leads the Atlas billing project at Acme.", "kind": "project_state", "importance": 4,
             "durability": "long", "about": "Atlas", "quote": "I lead Atlas"},
            {"text": "The Atlas API runs on localhost:8080.", "kind": "event", "importance": 3, "about": "Atlas"},
            {"text": "Ana has a doctor appointment tomorrow.", "kind": "transient", "importance": 2}]}


async def test_typed_per_conversation_candidates(tmp_path, hub):
    h = tmp_path / "home"
    chats(h, 1, per_file=3)
    llm = TypedLLM()
    m, _ = await _mine(hub, h, llm)
    st = await m.h_status({})
    texts = [c["text"] for c in st["new"]]
    assert texts == ["Ana leads the Atlas billing project at Acme.", "Ana has a doctor appointment tomorrow."]
    assert st["new"][1]["durability"] == "short" and st["new"][1]["expires_at"]  # time-bound: kept with an expiry
    c = st["new"][0]
    assert c["kind"] == "project_state" and c["about"] == "Atlas" and c["topic"] == "Atlas billing service"
    assert c["date"] and st["groups"][0] == {"about": "Atlas", "count": 1}
    assert "in Claude Code" in llm.prompts[0] and "ONE conversation" in llm.prompts[0]
