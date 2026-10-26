"""0.17 Memory redesign: search bar with ⌘K, filters | dense list with multi-select | detail, bulk actions."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "gateway" / "src" / "hub_gateway" / "local_ui_static"


def test_layout_has_three_columns_and_toolbar():
    h = (S / "index.html").read_text()
    for el in ("me-filters", "me-bulk", "me-new", "me-sugg", "me-kbd", "me-b-pin", "me-b-move", "me-b-forget"):
        assert f'id="{el}"' in h, el
    assert 'class="card me-dispute hidden" id="me-new-card"' in h  # "New note" opens it
    assert '<div id="me-review" class="hidden">' in h  # "Suggestions" opens every decision (0.19)
    m = h[h.index('id="view-memoria"'):h.index('id="view-graph"')]
    assert m.index('id="me-review"') < m.index('id="me-inbox-card"') < m.index('class="me-grid me-grid3"')
    assert m.index('id="me-review"') < m.index('id="rl-card"') < m.index('class="me-grid me-grid3"')
    # your notes come right after the search bar; profile and files come after them
    assert m.index('class="card me-bar me-top"') < m.index('class="me-grid me-grid3"') < m.index('id="pf-card"')
    assert m.index('class="me-grid me-grid3"') < m.index('id="files-view"')
    assert ".me-grid3{grid-template-columns:228px" in (S / "local.css").read_text()


def test_memory_js_filters_bulk_shortcut():
    js = (S / "memory.js").read_text()
    for k in ("pinned:", "mine:", "shared:", "old:", "imported:", "d7:", "d30:", "older:"):
        assert k in js, k
    assert 'e.key.toLowerCase() === "k"' in js and "(e.metaKey || e.ctrlKey)" in js
    assert "confirm(`Forget ${picked.size} notes?" in js and "confirm(`Move ${picked.size} notes" in js  # explicit click
    assert "ev.shiftKey && lastPick" in js  # shift-click range select
    # the hub dashboard shares this file: new elements are optional there
    assert 'if (!$("me-filters")) return;' in js and 'on("me-new", "onclick"' in js
    assert (ROOT / "dashboard" / "static" / "memory.js").read_text() == js


def test_detail_is_translatable_but_user_text_is_not():
    i18n = (S / "i18n.js").read_text()
    skip = re.search(r'const SKIP = "([^"]+)"', i18n).group(1)
    assert "#me-list .t" in skip and "#me-detail .me-note" in skip and ".me-app" in skip
    assert "#me-detail," not in skip and "#me-list," not in skip
    from hub_gateway import i18n_es as L
    for k in ("Search your memory…", "New note", "Suggestions", "Include shared notes", "Clear filters", "Correct", "Note"):
        assert k in L.EXACT, k
    assert "{n} selected" in L.PAT
