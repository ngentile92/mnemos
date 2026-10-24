"""FTUE #198: first-run screens in English or Spanish, no jargon, empty states with a button."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"
DESK = Path(__file__).resolve().parents[2] / "desktop" / "ui" / "app.js"
JARGON = ("MCP", "token", "context", "stdio")


def _first_run_text(html):
    out = []
    for sec, end in (('id="welcome"', "</section>"), ('id="view-home"', "</main>")):
        i = html.index(sec)
        out += [t.strip() for t in re.findall(r">([^<>]+)<", html[i:html.index(end, i)]) if re.search("[A-Za-z]", t)]
    return out


def test_no_jargon_on_first_run_screens():
    for t in _first_run_text((STATIC / "index.html").read_text()):
        assert not any(j.lower() in t.lower() for j in JARGON), t


def test_every_first_run_string_has_spanish():
    i18n = (STATIC / "i18n.js").read_text()
    for t in _first_run_text((STATIC / "index.html").read_text()):
        assert f'"{t}"' in i18n, t
    assert '<script src="/ui/i18n.js">' in (STATIC / "index.html").read_text()
    assert "window.t" in (STATIC / "home.js").read_text() or 't("' in (STATIC / "home.js").read_text()
    assert "const ES =" in DESK.read_text() and 'T("Connected ✓")' in DESK.read_text()
