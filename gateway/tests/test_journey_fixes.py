"""0.20 user-journey fixes: the map shows topics, not import codes or filler words; a new memory keeps its tools."""
from pathlib import Path

from hub_gateway.local_ui import build_graph

STATIC = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"


def _labels(items):
    return {n["label"].lower() for n in build_graph(items)["nodes"]}


def test_undo_batch_tag_is_not_a_topic():
    items = [{"id": f"n{i}", "text": t, "tags": ["imp-1a2b3c4d", "import-grok"]} for i, t in
             enumerate(["Your sister is called Marta.", "Marta lives in Porto.", "You moved to Porto in 2020."])]
    labels = _labels(items)
    assert not any(l.startswith("imp-") or l.startswith("#imp-") for l in labels), labels
    assert "marta" in labels and "porto" in labels


def test_filler_words_are_not_topics():
    items = [{"id": f"n{i}", "text": t} for i, t in enumerate(
        ["About me: I like tea.", "About me: I run on Sundays.", "Sobre mí: acá vivo.", "Después del trabajo, Marta llama."])]
    labels = _labels(items)
    for junk in ("about", "sobre", "acá", "después"):
        assert junk not in labels, labels


def test_spaces_are_shown_by_name_everywhere():
    js = (STATIC / "local.js").read_text()
    assert "window.MNEMOS_LABEL" in js
    mem = (STATIC / "memory.js").read_text()
    # spLabel must be defined before the first use (a const used earlier breaks the whole Memory screen)
    assert mem.index("const spLabel") < mem.index("spLabel(c)")
    assert mem == (Path(__file__).resolve().parents[2] / "dashboard" / "static" / "memory.js").read_text()


def test_new_memory_keeps_search_new_note_and_correction():
    css = (STATIC / "local.css").read_text()
    for sel in (".me-bar", ".me-dispute", ".me-grid", "#empty-cta"):
        assert f"body.filesmode {sel}" not in css


def test_notes_from_the_app_name_it_plainly():
    src = (STATIC.parent / "local_ui.py").read_text()
    assert 'Implementation(name="Mnemos", version="app")' in src
    assert r"/^mcp \d/" in (STATIC / "memory.js").read_text()  # older notes ("mcp 0.1.0") read "Mnemos app" too
