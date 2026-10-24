"""0.17 first run in four steps, the Connect tab, and activity for the one-URL router tools."""
from pathlib import Path

from hub_gateway import local_activity as A

S = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"


def test_four_step_welcome():
    h = (S / "index.html").read_text()
    for i in range(1, 5):
        assert f'id="wl-p{i}"' in h
    assert 'id="wl-save"' in h and 'id="wl-copy"' in h and 'id="wl-steps"' in h
    js = (S / "local.js").read_text()
    # Save all needs a click and skips duplicates; the aha ticks from a read after step 4 opened
    assert '$("wl-save").onclick' in js and "filter((it) => !it.duplicate)" in js
    assert 'x.kind === "read" && Date.parse(x.ts) >= Date.parse(started)' in js
    assert 'api("/api/ftue", { step: "aha" })' in js


def test_router_tools_are_described():
    assert A.describe("memory_start", {}, {}) == ("read", "read what it knows about you")
    assert A.describe("memory_read", {}, {})[0] == "read"
    k, s = A.describe("memory_remember", {"text": "I like tea"}, {})
    assert k == "write" and s.startswith("saved “I like tea”")


def test_activity_summaries_translated_but_not_quotes():
    for f in ("home.js", "apps.js"):
        assert "trSum(e.summary)" in (S / f).read_text()
    from hub_gateway import i18n_es as L
    assert L.EXACT["read what it knows about you"] and L.EXACT["Connect"] == "Conectar"
