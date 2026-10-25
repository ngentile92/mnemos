"""v0.18 C: copy coach after every copy, bring what an AI knows (forced when Mnemos has nothing from it), and a
Claude or ChatGPT export .zip as a path that needs no AI."""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from hub_gateway import local_connect as C
from hub_gateway import local_import as li

ROOT = Path(__file__).resolve().parents[2]
ST = ROOT / "gateway/src/hub_gateway/local_ui_static"


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, v in files.items():
            z.writestr(n, json.dumps(v))
    return buf.getvalue()


def test_claude_export_memory_first_then_my_own_statements():
    mem = {"conversations_memory": "## Work\n- Works as a nurse in Lisbon\n- Prefers short answers",
           "memory_files": [{"path": "/profile.md", "content": "# Profile\n- Has a dog called Pip", "updated_at": "2026-10-01"}]}
    convs = [{"uuid": "1", "name": "Trip", "updated_at": "2026-10-02", "chat_messages": [
        {"sender": "human", "text": "I live in Lisbon with my partner. What should I pack?"},
        {"sender": "assistant", "text": "I am an assistant and I like helping."}]}]
    src, cands = li.export_bytes(_zip({"data/memories.json": mem, "data/conversations.json": convs}))
    assert src == "claude"
    assert cands[:3] == ["Works as a nurse in Lisbon", "Prefers short answers", "Has a dog called Pip"]
    assert any(c.startswith("I live in Lisbon with my partner.") for c in cands)
    assert not any("assistant" in c for c in cands) and not any("/profile.md" in c for c in cands)


def test_old_claude_memory_shape_and_chatgpt_export():
    src, cands = li.export_bytes(_zip({"memories.json": [{"conversations_memory": "- Loves climbing on weekends"}]}))
    assert src == "claude" and cands == ["Loves climbing on weekends"]
    gpt = [{"title": "x", "update_time": 1, "mapping": {"a": {"message": {"author": {"role": "user"}, "content": {"parts": ["My daughter is called Ana."]}}}}}]
    src, cands = li.export_bytes(_zip({"conversations.json": gpt}))
    assert src == "chatgpt" and cands and cands[0].startswith("My daughter is called Ana.")


def test_not_an_export_and_split_export_explain_themselves():
    with pytest.raises(ValueError, match="not a Claude or ChatGPT export"):
        li.export_bytes(_zip({"other.json": {}}))
    with pytest.raises(ValueError, match="split export"):
        li.export_bytes(_zip({"manifest.json": {"parts": []}}))


def test_known_counts_notes_from_each_ai():
    items = [{"source_app": "codex-mcp-client 0.50.0", "tags": []}, {"source_app": "claude-ai 0.1.0 via mnemos", "tags": []},
             {"source_app": "Mnemos import", "tags": ["import-chatgpt"]}, {"source_app": "x", "tags": ["import-auto", "import-cursor_chats"]},
             {"source_app": "demo", "tags": []}]
    k = C.known(items)
    assert k["codex"] == 1 and k["claude-desktop"] == 1 and k["claude-web"] == 1 and k["chatgpt"] == 1 and k["cursor"] == 1
    assert k["grok"] == 0 and k["claude-code"] == 0


def test_every_copy_goes_through_the_coach():
    for f in ("local.js", "connect.js", "remote.js", "bring.js"):
        js = (ST / f).read_text()
        assert "navigator.clipboard.writeText" not in js, f
    coach = (ST / "coach.js").read_text()
    assert '"paste"' in coach and "paste-pulse" in coach and '"blur"' in coach and '"focus"' in coach
    assert 'name: "clipboard-read"' in coach and 'p.state === "granted"' in coach  # never a permission prompt
    assert "@keyframes pastePulse" in (ST / "local.css").read_text()


def test_bring_is_forced_only_when_nothing_from_that_ai():
    js = (ST / "bring.js").read_text()
    assert "const forced = opts.forced && base === 0" in js and 'forced ? ""' in js
    assert "k > base" in js  # finishes when that AI's notes arrive
    for w in re.findall(r'(?:en|es): "([^"]+)"', js.split("const ASK")[1].split("};")[0]):
        assert not re.search(r"\b(MCP|tool|memory_\w+)\b", w)
