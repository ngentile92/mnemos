"""v0.18 first run: an answer pasted from Grok counts as what Grok knows."""
from hub_gateway import local_connect as C, local_import as I


def test_grok_answer_is_its_own_source():
    assert "grok" in I.SOURCES
    assert C.known([{"tags": ["import-grok"]}])["grok"] == 1
    assert C.known([{"tags": ["import-text"]}])["grok"] == 0


def test_map_skips_verbs_and_source_tags():
    from hub_gateway.local_ui import entities, build_graph
    assert entities("Prefers short answers") == {"?Prefers"}
    assert entities("Carlos is my brother") == {"Carlos"}
    assert entities("- I'm planning a trip to Japan") == {"Japan"}
    g = build_graph([{"id": "1", "text": "Trip to Japan", "tags": ["import-grok"]}])
    assert "import-grok" not in [n["label"] for n in g["nodes"]]
