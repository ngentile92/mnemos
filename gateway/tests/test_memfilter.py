import asyncio
import datetime as dt

from hub_gateway import memfilter as m


def test_trivia_is_dropped():
    for t in ["The dev server runs on localhost:5173.", "See https://example.com/docs for it.",
              "The file src/app/main.py is broken.", "TypeError: x is undefined in the parser",
              "She ran `make build` again.", "The commit a3f9c21 broke CI.", "Ok perfect, go ahead.",
              "The assistant suggested using Redis.", "Python is a programming language."]:
        assert not m.classify(t)["keep"], t


def test_durable_is_kept():
    for t in ["Lucía works as a data engineer at Norte Logística.", "Prefers short answers in Spanish.",
              "Wants commit messages in English.", "Her manager is Tomás Iriarte."]:
        assert m.keep_texts([t]) == [t], t


def test_sensitive_off_by_default_and_opt_in():
    t = "She is taking sertraline for anxiety."
    c = m.classify(t)
    assert not c["keep"] and c["sensitive"] == "health"
    assert m.classify(t, allow_sensitive=True)["keep"]
    assert not m.classify("Her DNI number is 33.456.789.")["keep"]


def test_transient_gets_expiry():
    c = m.classify("She is travelling to Córdoba next week.", now=dt.date(2026, 1, 1))
    assert c["keep"] and c["durability"] == "short" and c["expires_at"] == "2026-01-15"
    assert m.keep_texts(["She is travelling to Córdoba next week."]) == []


def test_clean_message_strips_code_and_pastes():
    assert m.clean_message("ok") == ""
    assert m.clean_message("x" * 9000) == ""
    out = m.clean_message("I prefer tabs.\n```py\nprint(1)\n```\n> quoted mail\nTraceback (most recent call last):")
    assert out == "I prefer tabs."


class FakeLLM:
    name = kind = "fake"

    def __init__(self):
        self.prompts = []

    async def json(self, prompt):
        self.prompts.append(prompt)
        return {"topic": "Huerta garden app", "facts": [
            {"text": "Lucía is building Huerta with SvelteKit.", "kind": "project_state", "importance": 4,
             "durability": "long", "quote": "I'm building Huerta"},
            {"text": "The endpoint is /api/v2/plots.", "kind": "event", "importance": 2},
            {"text": "She fixed a bug today.", "kind": "event", "importance": 2, "durability": "short"},
            {"text": "Travelling to Córdoba next week.", "kind": "transient", "importance": 3},
            "Lucía is building Huerta with SvelteKit."]}


def test_extract_conversation_typed_and_gated():
    llm = FakeLLM()
    facts = asyncio.run(m.extract_conversation(llm, ["I'm building Huerta with SvelteKit", "ok", "```x```", "Off to Córdoba next week"], app="cursor",
                                               ts="2026-04-01"))
    texts = [f["text"] for f in facts]
    assert texts == ["Lucía is building Huerta with SvelteKit.", "Travelling to Córdoba next week."]
    assert facts[0]["kind"] == "project_state" and facts[0]["topic"] == "Huerta garden app"
    assert facts[1]["durability"] == "short" and facts[1]["expires_at"]
    assert "on 2026-04-01 in cursor" in llm.prompts[0] and "```" not in llm.prompts[0]


def test_windows_running_summary():
    llm = FakeLLM()
    asyncio.run(m.extract_conversation(llm, ["I like long walks in the park " * 40] * 12))
    assert len(llm.prompts) >= 2 and "So far it was about: Huerta garden app" in llm.prompts[1]


def test_grounded():
    from hub_gateway.memfilter import grounded
    src = "Trabajo en Pampa Pay y vivo en Rosario. Entreno los martes."
    assert grounded({"text": "Works at Pampa Pay.", "quote": "Trabajo en Pampa Pay"}, src)[0]
    assert grounded({"text": "Trains on Tuesdays.", "quote": ""}, src)[0]
    assert not grounded({"text": "Works at Google.", "quote": ""}, src)[0]
    assert not grounded({"text": "Lives in Rosario.", "quote": "vivo en Córdoba capital"}, src)[0]
    assert not grounded({"text": "Lives in Córdoba.", "quote": ""}, src)[0]
