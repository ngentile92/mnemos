"""Memory health on the files store (0.14): the same checks, bulk accept, backup and undo as on the notes store."""
from __future__ import annotations

import pytest

from hub_gateway import local
import test_memory_health as base


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOS_STORE", "files")
    monkeypatch.setenv("MNEMOS_HYGIENE", "0")
    monkeypatch.delenv("HUB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("HUB_ANSWER_MODEL", raising=False)
    h = tmp_path / "Mnemos"
    local.init_home(h, ["personal", "work"], "Sam Rivera")
    from hub_gateway import memfiles
    assert memfiles.active(h)
    return h


async def test_files_junk_forget_and_undo(home):
    await base.test_junk_forget_and_move_then_undo(home)
    assert list((home / "data" / "backups").glob("health-*/memory"))


async def test_files_move_accept(home):
    await base.test_move_accept(home)


def test_health_card_not_hidden_in_files_mode():
    from pathlib import Path
    css = (Path(local.__file__).parent / "local_ui_static" / "local.css").read_text()
    rule = next(l for l in css.splitlines() if l.startswith("body.filesmode .me-bar"))
    assert ".me-inbox" not in rule
