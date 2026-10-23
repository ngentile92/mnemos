"""0.13 prep: the Docker hub can run a context on simple memory (HUB_MEMORY_STORE=files). Scratch dirs only."""
from __future__ import annotations

import json

from fastmcp import Client

from hub_gateway import app as app_mod
from hub_gateway.audit import Audit
from hub_gateway.config import Settings
from hub_gateway.memory import DatasetMap
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS


def data(res):
    return res.structured_content or json.loads(res.content[0].text)


async def test_hub_context_on_files(tmp_path, monkeypatch, skills_repo, policy_file):
    monkeypatch.setenv("HUB_CONTEXT", "personal")
    monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "gw" / "personal"))
    monkeypatch.setenv("HUB_MEMORY_STORE", "files")
    monkeypatch.delenv("HUB_MEMORY_DIR", raising=False)
    mcp = app_mod.build_server(Settings.from_env(), datasets=DatasetMap(DATASETS),
                               skills=SkillIndex(skills_repo, "personal"),
                               broker=SecretBroker(load_policy(str(policy_file), "personal"), None),
                               audit=Audit("personal", str(tmp_path / "a")))
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert {"memory_start", "memory_remember", "memory_read"} <= names
        r = data(await c.call_tool("memory_remember", {"text": "Ana prefers short answers", "tags": ["kind-preference"]}))
        assert r["file"] == "personal/profile.md"
        st = data(await c.call_tool("memory_start", {}))
        assert "short answers" in st["profile"]["personal"]
    assert (tmp_path / "gw" / "memory" / "personal" / "profile.md").exists()  # one repo next to the contexts' data
