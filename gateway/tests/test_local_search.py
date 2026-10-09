import httpx
from fastmcp import Client

from hub_gateway.local_search import LocalIndex, OllamaEmbedder, fts_query, query_terms
from conftest import DATASETS
from test_server import NOTE_IDS, data, make_server  # noqa: F401 (fixture)


def test_query_terms_drop_stopwords_and_accents():
    assert query_terms("¿Quién es la CTO de Brisa Labs?") == ["cto", "brisa", "labs"]
    assert fts_query("de la que") is None
    assert fts_query("lanzamiento Faro") == '"lanzamiento"* OR "faro"*'


async def _fill(idx, notes):
    items = [{"id": i, "createdAt": f"2026-10-0{n}"} for n, i in enumerate(notes, 1)]
    await idx.sync("personal", lambda: _ret(items), lambda did: _ret(notes[did]), force=True)


async def _ret(v):
    return v


async def test_keyword_search_bm25_accent_insensitive(tmp_path):
    idx = LocalIndex(str(tmp_path / "s.sqlite"))
    await _fill(idx, {"a": "Laura Méndez es la CTO de Brisa Labs.", "b": "Brisa Labs usa PostgreSQL.",
                      "c": "Café sin azúcar."})
    out = await idx.search("quien es la cto de brisa", ["personal"], 5, "keyword")
    assert [r["id"] for r in out][:2] == ["a", "b"] and out[0]["match"] == "keyword"
    assert (await idx.search("azucar", ["personal"], 5, "keyword"))[0]["id"] == "c"
    assert await idx.search("azucar", ["work"], 5, "keyword") == []  # other datasets never leak


async def test_sync_adds_and_removes(tmp_path):
    idx = LocalIndex(str(tmp_path / "s.sqlite"))
    await _fill(idx, {"a": "uno", "b": "dos"})
    await _fill(idx, {"b": "dos"})
    assert idx.ids("personal") == {"b"}


async def test_semantic_and_hybrid_with_fake_ollama(tmp_path):
    vocab = ["gimnasio", "ejercicio", "cafe"]

    def handler(req):
        import json
        texts = json.loads(req.content)["input"]
        return httpx.Response(200, json={"embeddings": [
            [1.0 if any(w in t.lower() for w in (v, "entrenar" if v == "ejercicio" else v)) else 0.0 for v in vocab]
            for t in texts]})

    emb = OllamaEmbedder("http://ollama", "m", transport=httpx.MockTransport(handler))
    idx = LocalIndex(str(tmp_path / "s.sqlite"), emb)
    await _fill(idx, {"g": "Voy al gimnasio y hago ejercicio.", "c": "Tomo cafe."})
    sem = await idx.search("quiero entrenar", ["personal"], 5, "semantic")
    assert sem[0]["id"] == "g"
    hyb = await idx.search("cafe", ["personal"], 5, "hybrid")
    assert hyb[0]["id"] == "c" and "keyword" in hyb[0]["match"]


async def test_semantic_degrades_when_ollama_down(tmp_path):
    emb = OllamaEmbedder("http://ollama", "m", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    idx = LocalIndex(str(tmp_path / "s.sqlite"), emb)
    await _fill(idx, {"a": "Brisa Labs"})
    assert await idx.search("brisa", ["personal"], 5, "semantic") == []
    assert (await idx.search("brisa", ["personal"], 5, "hybrid"))[0]["id"] == "a"


async def test_memory_search_keyword_mode_returns_ids_scoped(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_search", {"query": "orion", "mode": "keyword"}))
        own = data(await c.call_tool("memory_search", {"query": "orion", "mode": "keyword", "include_shared": False}))
    assert {r["id"] for r in out["results"]} == {NOTE_IDS["personal"], NOTE_IDS["shared"]}
    assert [r["id"] for r in own["results"]] == [NOTE_IDS["personal"]]
    assert rec.recalls == []  # Cognee's graph search not used
    assert all(r["id"] != NOTE_IDS["work"] for r in out["results"])


async def test_auto_mode_uses_local_hits_else_graph(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        hit = data(await c.call_tool("memory_search", {"query": "orion"}))
        miss = data(await c.call_tool("memory_search", {"query": "zzz inexistente"}))
    assert hit["mode"] == "auto" and hit["results"][0]["id"] in NOTE_IDS.values()
    assert len(rec.recalls) == 1 and miss["results"]  # only the miss went to Cognee's graph
