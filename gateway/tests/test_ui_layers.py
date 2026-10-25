"""Layout guards: the More menu above cards, graph toolbar not using the progress-bar class, import rows full width,
and the click-reachability check wired into CI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ST = ROOT / "gateway/src/hub_gateway/local_ui_static"


def test_header_above_cards():
    css = (ST / "local.css").read_text()
    assert "header.top{position:relative;z-index:50}" in css and ".tab-menu" not in css  # no More menu since v0.18


def test_graph_toolbar_class():
    html = (ST / "index.html").read_text()
    assert 'class="card me-bar g-toolbar"' in html and "me-bar g-bar" not in html


def test_import_rows_full_width():
    assert "label.imp-item:has(>input){display:flex;width:100%" in (ST / "local.css").read_text()


def test_reach_check_in_ci():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "scripts/ui_reach_check.py" in ci and (ROOT / "scripts/ui_reach_check.py").exists()
