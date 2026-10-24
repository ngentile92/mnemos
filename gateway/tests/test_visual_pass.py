from pathlib import Path

S = Path(__file__).resolve().parents[1] / "src" / "hub_gateway" / "local_ui_static"


def test_visual_pass_fixes():
    css = (S / "local.css").read_text()
    assert ".me-inbox{margin:0 36px 14px}" in css and ".sy-ctl{" in css
    assert 'class="small sy-ctl"' in (S / "sync.js").read_text()
    js = (S / "local.js").read_text()
    assert '"home folder"' in js and 's.count === 1' in js
    assert "ap-refresh\\').click()\">Refresh" not in (S / "apps.js").read_text()  # one Refresh, in the header
