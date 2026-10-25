"""0.17 first run in four steps, the Connect tab, and activity for the one-URL router tools."""
from pathlib import Path

from hub_gateway import local_activity as A

S = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"


def test_first_run_is_one_loop_per_ai():
    h = (S / "index.html").read_text()
    for i in range(1, 4):
        assert f'id="wl-p{i}"' in h
    assert 'id="wl-pick"' in h and 'id="wl-steps"' in h and 'id="reveal"' in h and '<script src="/ui/onboarding.js">' in h
    js = (S / "onboarding.js").read_text()
    # per AI: connect (auto-detected by its first call) → bring what it knows (forced when we have nothing) → use it every time
    assert "connectStep" in js and "bringStep" in js and "defaultStep" in js and "forced: true" in js
    assert '"#graph?reveal=1"' in js and 'mark("aha")' in js
    assert "window.MnemosOnboarding.start(" in (S / "local.js").read_text()


def test_router_tools_are_described():
    assert A.describe("memory_start", {}, {}) == ("read", "read what it knows about you")
    assert A.describe("memory_read", {}, {})[0] == "read"
    k, s = A.describe("memory_remember", {"text": "I like tea"}, {})
    assert k == "write" and s.startswith("saved “I like tea”")


def test_activity_summaries_translated_but_not_quotes():
    for f in ("apps.js",):
        assert "trSum(e.summary)" in (S / f).read_text()
    from hub_gateway import i18n_es as L
    assert L.EXACT["read what it knows about you"] and L.EXACT["Connect"] == "Conectar"


def test_final_pass_strings_translated():
    from hub_gateway import i18n_es
    for k in ["(not found on this computer)", "can read", "On", "file", "files",
              "Shared holds what every space should know: your name, language, how you like answers. Every space and every app can read it. Notes only get there when you choose “Promote to shared”."]:
        assert k in i18n_es.EXACT, k
    assert "key: {x} (never changes)" in i18n_es.PAT


def test_final_pass_css_and_labels():
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/hub_gateway/local_ui_static"
    css = (st / "local.css").read_text()
    assert "label.small input[type=checkbox]" in css and ".sp-row .sp-label{width:100%" in css
    assert "spLabel(s)" in (st / "sync.js").read_text()
