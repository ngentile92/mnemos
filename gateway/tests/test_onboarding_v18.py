"""v0.18 first run: an answer pasted from Grok counts as what Grok knows."""
from hub_gateway import local_connect as C, local_import as I


def test_grok_answer_is_its_own_source():
    assert "grok" in I.SOURCES
    assert C.known([{"tags": ["import-grok"]}])["grok"] == 1
    assert C.known([{"tags": ["import-text"]}])["grok"] == 0
