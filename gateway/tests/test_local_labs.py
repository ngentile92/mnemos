"""Labs (0.11): optional layers off by default, a setting per home, MNEMOS_LABS wins."""

from hub_gateway import local_labs


def test_off_by_default_and_toggle(tmp_path, monkeypatch):
    monkeypatch.delenv("MNEMOS_LABS", raising=False)
    assert local_labs.enabled(tmp_path) is False
    assert local_labs.set_enabled(tmp_path, True) == {"on": True, "forced": False}
    assert local_labs.enabled(tmp_path) is True
    local_labs.set_enabled(tmp_path, False)
    assert local_labs.enabled(tmp_path) is False


def test_env_wins(tmp_path, monkeypatch):
    local_labs.set_enabled(tmp_path, False)
    monkeypatch.setenv("MNEMOS_LABS", "1")
    assert local_labs.status(tmp_path) == {"on": True, "forced": True}
    monkeypatch.setenv("MNEMOS_LABS", "off")
    local_labs.set_enabled(tmp_path, True)
    assert local_labs.enabled(tmp_path) is False


def test_static_marks_labs_features():
    from pathlib import Path
    html = (Path(local_labs.__file__).parent / "local_ui_static" / "index.html").read_text()
    assert 'class="tab labs" data-view="graph"' in html and 'id="labs-on"' in html
    assert 'class="card labs"' in html and "labs" in html.split('id="pf-card"')[0].rsplit("<section", 1)[1]
