"""v0.20: the Grok path (copy and paste) end to end: its answer is read cleanly, counted as Grok's, and the app shows
where to paste what Mnemos knows."""
from pathlib import Path

from hub_gateway import local_connect
from hub_gateway.local_import import candidates, from_lines

STATIC = Path(__file__).resolve().parents[1] / "src/hub_gateway/local_ui_static"

GROK_ANSWER = """Here's everything I know about you based on our past conversations:

```
- You're a software engineer based in Buenos Aires.
- Your partner is Laura.
```

**Preferences**
1. You prefer short, direct answers.
2. **Tools:** You use VS Code and Python.

### Goals
* Run a marathon in 2027.

Let me know if you'd like me to update anything!"""


def test_grok_answer_keeps_facts_only():
    got = candidates("grok", {"text": GROK_ANSWER})
    assert got == ["You're a software engineer based in Buenos Aires.", "Your partner is Laura.",
                   "You prefer short, direct answers.", "Tools: You use VS Code and Python.", "Run a marathon in 2027."]


def test_spanish_answer_drops_the_chat_around_it():
    assert from_lines("¡Claro! Acá está todo lo que sé de vos:\n- Vivís en Córdoba.\n- Tenés dos hijos.\n"
                      "¿Querés que agregue algo más?") == ["Vivís en Córdoba.", "Tenés dos hijos."]


def test_grok_notes_count_as_grok():
    assert local_connect.known([{"tags": ["import-grok"]}, {"tags": ["import-claude"]}])["grok"] == 1


def test_ui_shows_the_whole_loop():
    html = (STATIC / "index.html").read_text()
    assert 'data-src="grok"' in html and "Grok → Settings → Customize → Custom instructions" in html
    js = (STATIC / "connect.js").read_text()
    assert "data-give" in js and "grok.com/connectors" in js
    bring = (STATIC / "bring.js").read_text()
    assert "giveBack" in bring and "grok:" in bring
    assert 'previewJSON(cloudSrc' in (STATIC / "local.js").read_text()  # not always counted as Claude
