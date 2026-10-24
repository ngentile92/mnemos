"""UX phase 1: real counts on Home, names people know, grouped navigation, no empty viewers."""
from pathlib import Path

from hub_gateway.local_apps import friendly_label

UI = Path(__file__).resolve().parents[1] / "src/hub_gateway/local_ui_static"


def test_friendly_labels():
    assert friendly_label("local-agent-mode-mnemos", "local-agent-mode-mnemos") == "Claude Desktop"
    assert friendly_label("claude-ai", "claude-ai") == "Claude Desktop"
    assert friendly_label("my-tool", "My Tool!") == "My Tool!"


def test_home_uses_real_counts_not_the_list_cap():
    js = (UI / "home.js").read_text()
    assert "/api/memory/counts" in js and "/api/memory/list" not in js
    assert 'path == "memory/counts"' in (UI.parent / "local_ui.py").read_text()


def test_navigation_is_grouped():
    html = (UI / "index.html").read_text()
    nav = html.split('id="tabs"')[1].split("</nav>")[0]
    assert 'class="tab sec"' not in nav  # .sec is a grid layout class elsewhere: it spread the tabs apart
    top, more = nav.split('id="tab-more"')
    for v in ("home", "memoria", "native", "apps", "import"):
        assert f'data-view="{v}"' in top
    for v in ("spaces", "skills", "graph", "settings"):
        assert f'data-view="{v}"' in more
    assert "Engine:" not in nav


def test_no_empty_viewer_and_styled_controls():
    assert "Pick a file." not in (UI / "native.js").read_text()
    css = (UI / "local.css").read_text()
    assert "input[type=text]" in css and "accent-color" in css and ".empty{" in css
