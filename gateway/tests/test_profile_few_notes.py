"""A new memory in files mode has a profile and real topics after a handful of notes, with no model."""
import json

from fastmcp import Client

from hub_gateway import local


def data(r):
    return r.structured_content or json.loads(r.content[0].text)


async def test_profile_and_topics_from_five_notes(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.setenv("MNEMOS_STORE", "files")
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Ana")
    s = local.build_all(h, 8765)
    async with Client(s["personal"]) as p:
        for t in ["Me llamo Ana", "Vivo en Madrid", "Prefiero respuestas cortas y sin emojis",
                  "Mi hermana Laura vive en Lisboa", "Estoy aprendiendo a tocar la guitarra"]:
            await p.call_tool("memory_remember", {"text": t})
        st = data(await p.call_tool("memory_start", {}))
    assert "Me llamo Ana" in st["profile"]["personal"]
    assert {t["topic"] for t in st["topics"]} >= {"people", "goals"}
    assert "notes" not in {t["topic"] for t in st["topics"]}
