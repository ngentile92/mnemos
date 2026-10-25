"""Empty-memory path: per-app reasons when the scan finds little, Connect cards incl. ChatGPT, and the
"fill your memory from your past chats" request (plain words, EN/ES, no tool names)."""
import re
from pathlib import Path

from hub_gateway import i18n_es as L
from hub_gateway import local_discover as d

ST = Path(__file__).resolve().parents[1] / "src/hub_gateway/local_ui_static"


def test_empty_home_scan_and_reasons(tmp_path):
    found = d.scan(tmp_path, ["personal"])
    assert found == []
    apps = {a["id"]: a for a in d.presence(tmp_path, found)}
    assert apps["claude-desktop"]["state"] == "cloud" and apps["chatgpt"]["state"] == "cloud"
    assert apps["claude-code"]["state"] == "absent" and apps["cursor"]["state"] == "absent"
    for a in apps.values():
        assert a["why"] in L.EXACT, a["why"]


def test_installed_but_empty_and_found(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("# Me\n- I like short answers\n")
    (tmp_path / ".cursor").mkdir()
    found = d.scan(tmp_path, ["personal"])
    apps = {a["id"]: a["state"] for a in d.presence(tmp_path, found)}
    assert apps["claude-code"] == "found" and apps["cursor"] == "empty"


def test_connect_cards_include_chatgpt_and_bootstrap():
    js = (ST / "connect.js").read_text()
    html = (ST / "index.html").read_text()
    assert 'key: "chatgpt"' in js and 'id="cn-grid"' in html and 'id="bs-card"' in html
    assert '<script src="/ui/connect.js"></script>' in html
    boots = re.findall(r'(?:en|es): "([^"]+)"', js.split("const BOOT")[1].split("};")[0])
    assert len(boots) == 2
    for b in boots:
        assert not re.search(r"\b(MCP|tool|tools|memory_\w+|herramienta)\b", b, re.I), b
    for s in re.findall(r'T\("([^"]+)"\)', js):
        assert s in L.EXACT, s
    for steps in re.findall(r'\["([^\]]+)"\]', js.split("const STEPS")[1].split("};")[0]):
        for s in steps.split('", "'):
            assert s in L.EXACT, s
