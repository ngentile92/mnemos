import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hub_gateway.answer import Answerer, parse_reply
from test_server import NOTE_IDS, data, make_server  # noqa: F401 (fixture)

NOTES = [{"id": "a", "dataset": "personal", "text": "Laura es CTO."}, {"id": "b", "dataset": "personal", "text": "x"}]


def test_parse_reply_validates_citations():
    out = parse_reply('{"known": true, "answer": "Laura [1]", "citations": [1, "[2]", 7, "z"], "unknown": []}', NOTES)
    assert out["known"] and [c["id"] for c in out["citations"]] == ["a", "b"]
    no_cite = parse_reply('{"known": true, "answer": "Laura", "citations": []}', NOTES)
    assert no_cite["known"] is False and no_cite["citations"] == []  # an uncited answer is not trusted
    assert parse_reply("basura", NOTES)["known"] is False
    assert parse_reply('texto {"known": false, "answer": "", "unknown": ["teléfono"]} fin', NOTES)["unknown"] == ["teléfono"]


async def test_answerer_no_notes_is_unknown():
    out = await Answerer("http://x", "m").answer("¿teléfono?", [])
    assert out["known"] is False and out["unknown"] == ["¿teléfono?"]


async def test_memory_answer_tool(make_server, monkeypatch):
    seen = {}

    def ollama(req):
        body = json.loads(req.content)
        seen["prompt"] = body["messages"][1]["content"]
        return httpx.Response(200, json={"message": {"content": json.dumps(
            {"known": True, "answer": "Habla de orion [1]", "citations": [1], "unknown": []})}})

    server, rec = make_server("personal")
    async with Client(server) as c:
        with pytest.raises(ToolError, match="no está configurado"):
            await c.call_tool("memory_answer", {"question": "qué sé de orion"})
    from hub_gateway import app as app_mod

    monkeypatch.setattr(app_mod.Answerer, "from_env",
                        classmethod(lambda cls: cls("http://ollama", "m", transport=httpx.MockTransport(ollama))))
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_answer", {"question": "qué sé de orion", "include_shared": False}))
    assert out["known"] and out["citations"][0]["id"] == NOTE_IDS["personal"]
    assert "nota de personal sobre orion" in seen["prompt"] and "work" not in seen["prompt"]
