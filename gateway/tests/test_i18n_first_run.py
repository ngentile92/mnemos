"""FTUE #198: first-run screens in English or Spanish, no jargon, empty states with a button."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"
DESK = Path(__file__).resolve().parents[2] / "desktop" / "ui" / "app.js"
JARGON = ("MCP", "token", "context", "stdio")


def _first_run_text(html):
    """Every visible string of every screen (the whole page), plus placeholders and tooltips."""
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    out = []
    for t in re.findall(r">([^<>]+)<", html) + re.findall(r'(?:placeholder|title)="([^"]+)"', html):
        t = " ".join(t.split())
        if re.search("[A-Za-z]{2}", t) and t not in out:
            out.append(t)
    return out


JS_STRINGS = ["Nothing to review. ✓", "No skills yet.", "Show me the next step", "Scan again", "Keep as is", "Remove access",
              "No folders mapped yet.", "Nothing found on this computer. Use the options below.", "Saved ✓", "Try it in your AI"]


def test_script_strings_have_spanish_and_exist():
    i18n = (STATIC / "i18n.js").read_text()
    js = "".join(p.read_text() for p in STATIC.glob("*.js"))
    for t in JS_STRINGS:
        assert t in js.replace(f'"{t}": ', ""), t
        assert f'"{t}"' in i18n, t
    assert "MutationObserver" in i18n and "SKIP" in i18n  # dynamic text translated, user content left alone


def test_no_jargon_on_first_run_screens():
    for t in _first_run_text((STATIC / "index.html").read_text()):
        assert not any(re.search(rf"\b{j}\b", t, re.I) for j in JARGON), t


def test_every_first_run_string_has_spanish():
    i18n = (STATIC / "i18n.js").read_text()
    for t in _first_run_text((STATIC / "index.html").read_text()):
        assert f'"{t}"' in i18n, t
    assert '<script src="/ui/i18n.js">' in (STATIC / "index.html").read_text()
    assert "window.t" in (STATIC / "home.js").read_text() or 't("' in (STATIC / "home.js").read_text()
    assert "const ES =" in DESK.read_text() and 'T("Connected ✓")' in DESK.read_text()
