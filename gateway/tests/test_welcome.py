"""First run (FTUE #188, #195): welcome saves the name as the owner every AI reads; MVP tabs first."""

from pathlib import Path

import pytest
import yaml

from hub_gateway import local_welcome

STATIC = Path(local_welcome.__file__).parent / "local_ui_static"


def _home(tmp_path, owner="the owner"):
    (tmp_path / "contexts.yaml").write_text(yaml.safe_dump({"owner": owner, "contexts": {
        "personal": {"description": "personal life"}, "work": {"description": "my job"}}}))
    return tmp_path


def test_status_and_finish(tmp_path):
    h = _home(tmp_path)
    st = local_welcome.status(h)
    assert st["done"] is False and st["name"] == ""
    assert [s["label"] for s in st["spaces"]] == ["Personal", "Work"]
    out = local_welcome.finish(h, "  Ada   Lovelace ")
    assert out["done"] is True and out["name"] == "Ada Lovelace"
    assert yaml.safe_load((h / "contexts.yaml").read_text())["owner"] == "Ada Lovelace"
    assert (h / "data" / "contexts.yaml.bak").exists()


def test_finish_without_name_keeps_owner(tmp_path):
    h = _home(tmp_path, owner="Sam")
    assert local_welcome.finish(h)["name"] == "Sam"
    assert local_welcome.done(h)


def test_name_too_long(tmp_path):
    with pytest.raises(ValueError):
        local_welcome.finish(_home(tmp_path), "x" * 61)
    assert not local_welcome.done(tmp_path)


def test_ui_welcome_and_tabs():
    html = (STATIC / "index.html").read_text()
    assert 'id="welcome"' in html and 'id="wl-name"' in html and 'id="wl-go"' in html
    w = html.split('id="welcome"')[1].split("</section>")[0]
    assert w.count("<input") == 1 and "context" not in w.lower()
    nav = html.split('id="tabs"')[1].split("</nav>")[0]
    main = [t for t in nav.split("<a ")[1:] if 'class="tab"' in t or 'class="tab active"' in t]
    assert [t.split(">")[1].split("<")[0] for t in main] == ["Memory", "Apps", "Skills"]
    for word in ("Docker", "Reach", "Labs", "secret", "store"):
        assert word not in nav
    js = (STATIC / "local.js").read_text()
    assert "needsWelcome" in js and '"/api/welcome"' in js
