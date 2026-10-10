"""Local skills folder: create/edit from the dashboard, validated like the gateway; POST guard."""
from __future__ import annotations

import importlib.util
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))
import skills_store  # noqa: E402
from hub_gateway.skills import SkillError, SkillIndex  # noqa: E402

GOOD = "---\nname: my-skill\ndescription: Does a thing when asked.\n---\n\n# my-skill\n"


def test_create_edit_and_gateway_sees_it(tmp_path):
    skills_store.ensure_layout(tmp_path)
    res = skills_store.save(tmp_path, "shared", GOOD)
    assert res["created"] and (tmp_path / res["path"]).read_text() == GOOD
    with pytest.raises(SkillError, match="already exists"):
        skills_store.save(tmp_path, "personal", GOOD)  # names are global
    skills_store.save(tmp_path, "shared", GOOD.replace("Does a thing", "Does another thing"), edit="my-skill")
    idx = SkillIndex(tmp_path, "personal", min_interval=0)
    assert idx.get("my-skill").description.startswith("Does another thing")


@pytest.mark.parametrize("content,msg", [
    ("no frontmatter", "frontmatter"),
    ("---\nname: Bad Name\ndescription: x\n---\n", "lowercase"),
    ("---\nname: ok-name\n---\n", "description"),
    ("---\nname: ok-name\ndescription: d\nmetadata:\n  hub-share: nope\n---\n", "hub-share"),
])
def test_invalid_rejected(tmp_path, content, msg):
    with pytest.raises(SkillError, match=msg):
        skills_store.save(tmp_path, "shared", content)
    assert not any((tmp_path / "skills").rglob("SKILL.md")) if (tmp_path / "skills").exists() else True


def test_rename_and_unknown_owner_rejected(tmp_path):
    skills_store.save(tmp_path, "shared", GOOD)
    with pytest.raises(SkillError, match="renaming"):
        skills_store.save(tmp_path, "shared", GOOD.replace("my-skill", "other"), edit="my-skill")
    with pytest.raises(SkillError, match="owner"):
        skills_store.save(tmp_path, "../etc", GOOD)


def test_local_root(tmp_path, monkeypatch):
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    assert skills_store.local_root(tmp_path, {}) is None  # GitHub mode (Nico's setup: no SKILLS_DIR)
    assert skills_store.local_root(tmp_path, {"SKILLS_DIR": "./skills-local"}) == tmp_path / "skills-local"


@pytest.fixture
def dash(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mnemos_dashboard_w", ROOT / "dashboard" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "skills_dir", lambda: tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(None, set(), None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = mod.make_handler(None, {f"127.0.0.1:{port}", f"mac.tail1.ts.net:8444"}, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, tmp_path, mod
    srv.shutdown()


def _post(port, body, **hdr):
    h = {"Origin": f"http://127.0.0.1:{port}", "X-Mnemos-Write": "1", "Content-Type": "application/json", **hdr}
    return httpx.post(f"http://127.0.0.1:{port}/api/skills/save", json=body, headers=h)


def test_post_saves(dash):
    port, root, _ = dash
    r = _post(port, {"owner": "shared", "content": GOOD})
    assert r.status_code == 200 and (root / "skills/shared/my-skill/SKILL.md").exists()
    assert _post(port, {"owner": "shared", "content": "nope"}).status_code == 400


@pytest.mark.parametrize("hdr", [{"Origin": "https://evil.example"}, {"X-Mnemos-Write": ""},
                                 {"Host": "mac.tail1.ts.net:8444"}, {"Content-Type": "text/plain"}])
def test_post_guard(dash, hdr):
    port, root, _ = dash
    if "Host" in hdr:
        hdr = {**hdr, "Origin": "http://mac.tail1.ts.net:8444"}
    assert _post(port, {"owner": "shared", "content": GOOD}, **hdr).status_code == 403
    assert not (root / "skills").exists()


def test_post_github_mode_refuses(dash, monkeypatch):
    port, _, mod = dash
    monkeypatch.setattr(mod, "skills_dir", lambda: None)
    assert _post(port, {"owner": "shared", "content": GOOD}).status_code == 409
