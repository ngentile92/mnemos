"""Harvest lane: memories other AI apps already curated (Claude Code auto-memory, Codex memories)."""
import time

from hub_gateway import local_discover as ld


def test_scan_and_read_vendor_memories(tmp_path):
    mem = tmp_path / ".claude" / "projects" / "-Users-ana-code-atlas" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [User](user.md)\n- [Feedback](feedback.md)\n")
    (mem / "user.md").write_text("---\nname: user\ntype: user\n---\n# User\n- Ana is a backend engineer at Acme\n"
                                 "- Ana prefers small PRs with tests\n- Ana is in therapy for anxiety\n")
    cx = tmp_path / ".codex" / "memories"
    cx.mkdir(parents=True)
    (cx / "notes.md").write_text("* Ana deploys Atlas on Fridays only\n* token: abcdefghijklmnop\n")
    found = {s["kind"]: s for s in ld.scan(tmp_path, ["work"])}
    assert found["claude_memory"]["count"] == 2 and found["codex_memory"]["count"] == 1
    notes, msgs = ld.read_source(found["claude_memory"], time.monotonic() + 10)
    assert msgs == [] and notes == ["Ana is a backend engineer at Acme", "Ana prefers small PRs with tests"]
    notes, _ = ld.read_source(found["codex_memory"], time.monotonic() + 10)
    assert notes == ["Ana deploys Atlas on Fridays only"]
