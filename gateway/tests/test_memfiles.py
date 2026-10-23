"""Simple memory (0.11): Markdown files + git as the note store."""

import asyncio
from pathlib import Path

import pytest

from hub_gateway.memfiles import FileNoteStore, MemoryRepo, bullets, route, split_front
from hub_gateway.memory import MemoryError_

DS = "11111111-1111-1111-1111-111111111111"
SH = "22222222-2222-2222-2222-222222222222"


def store(tmp_path, allowed=None):
    return FileNoteStore(tmp_path / "memory", allowed or {DS, SH}, {DS: "personal", SH: "shared"})


def run(c):
    return asyncio.run(c)


def test_no_tags_uses_word_rules(tmp_path):
    s = store(tmp_path)
    assert run(s.remember("Me llamo Ana.", DS))["file"] == "personal/profile.md"
    assert run(s.remember("Mi hermana Laura vive en Lisboa.", DS))["file"] == "personal/topics/people.md"
    assert run(s.remember("Horses at the farm.", DS))["file"] == "personal/topics/notes.md"


def test_remember_routes_by_kind_and_commits_per_app(tmp_path):
    s = store(tmp_path)
    a = run(s.remember("Lives in Lisbon.", DS, metadata={"source_app": "claude-code", "tags": ["kind-identity"]}))
    b = run(s.remember("Sister Ana is a doctor.", DS, metadata={"source_app": "chatgpt", "tags": ["kind-relationship"]}))
    c = run(s.remember("Horses at the farm.", DS, metadata={"source_app": "Claude"}))
    assert a["file"] == "personal/profile.md" and b["file"] == "personal/topics/people.md"
    assert c["file"] == "personal/topics/notes.md"
    prof = (tmp_path / "memory/personal/profile.md").read_text()
    fm, body = split_front(prof)
    assert fm["kind"] == "profile" and fm["sources"] == ["claude-code"] and "- Lives in Lisbon." in body
    log = s.repo.history()
    assert [e["author"] for e in log[:3]] == ["Claude", "chatgpt", "claude-code"]
    ids = {d["id"] for d in run(s.list_data(DS))}
    assert ids == {a["items"][0]["id"], b["items"][0]["id"], c["items"][0]["id"]}


def test_same_text_is_one_note(tmp_path):
    s = store(tmp_path)
    a = run(s.remember("Likes horses.", DS))
    b = run(s.remember("Likes horses.", DS))
    assert a["items"][0]["id"] == b["items"][0]["id"] and len(run(s.list_data(DS))) == 1


def test_update_delete_keep_ids_and_history(tmp_path):
    s = store(tmp_path)
    nid = run(s.remember("Market data comes from Kaiko.", DS))["items"][0]["id"]
    other = run(s.remember("Database: Postgres.", DS))["items"][0]["id"]
    run(s.update(DS, nid, "Market data comes from Tiingo."))
    assert run(s.raw_text(DS, nid)) == "Market data comes from Tiingo."
    assert run(s.raw_text(DS, other)) == "Database: Postgres."
    run(s.delete_data(DS, other))
    assert run(s.raw_text(DS, other)) is None
    with pytest.raises(MemoryError_):
        run(s.delete_data(DS, other))
    assert len(s.repo.history("personal/topics/notes.md")) == 4


def test_supersede_moves_to_no_longer_true(tmp_path):
    s = store(tmp_path)
    nid = run(s.remember("Market data: Kaiko.", DS))["items"][0]["id"]
    run(s.remember("Market data: Tiingo.", DS))
    run(s.supersede(DS, nid))
    text = (tmp_path / "memory/personal/topics/notes.md").read_text()
    assert "## No longer true" in text and "~~Market data: Kaiko.~~" in text
    assert [d["id"] for d in run(s.list_data(DS))] != [nid] and nid not in {d["id"] for d in run(s.list_data(DS))}
    assert run(s.raw_text(DS, nid)) == "Market data: Kaiko."  # still readable as history
    run(s.remember("Jazz on Fridays.", DS))  # new notes go above the past section
    body = split_front((tmp_path / "memory/personal/topics/notes.md").read_text())[1]
    bs = bullets(body)
    assert [b["text"] for b in bs if not b["past"]] == ["Market data: Tiingo.", "Jazz on Fridays."]


def test_hand_edit_keeps_ids_and_is_committed_as_you(tmp_path):
    s = store(tmp_path)
    a = run(s.remember("Lives in Lisbon.", DS))["items"][0]["id"]
    b = run(s.remember("Has two cats.", DS))["items"][0]["id"]
    p = tmp_path / "memory/personal/topics/notes.md"
    p.write_text(p.read_text().replace("Has two cats.", "Has three cats.") + "- Written by hand.\n")
    items = {d["id"]: run(s.raw_text(DS, d["id"])) for d in run(s.list_data(DS))}
    assert items[a] == "Lives in Lisbon." and items[b] == "Has three cats."
    assert "Written by hand." in items.values()
    assert s.repo.history()[0]["author"] == "you" and s.repo.history()[0]["message"] == "Edited by hand"


def test_multiline_notes_round_trip(tmp_path):
    s = store(tmp_path)
    text = "Trip plan:\nfly to Rome\n\nthen Naples"
    nid = run(s.remember(text, DS))["items"][0]["id"]
    assert run(s.raw_text(DS, nid)) == text


def test_scope_is_enforced(tmp_path):
    s = store(tmp_path, allowed={DS})
    with pytest.raises(MemoryError_):
        run(s.remember("x" * 10, SH))
    assert run(s.raw_text(SH, "nope")) is None


def test_restore_and_undo(tmp_path):
    s = store(tmp_path)
    run(s.remember("Likes horses.", DS))
    first = s.repo.history()[0]["sha"]
    nid = run(s.remember("Likes jazz.", DS))["items"][0]["id"]
    second = s.repo.history()[0]["sha"]
    s.repo.undo(second)
    assert run(s.raw_text(DS, nid)) is None
    s.restore(DS, "personal/topics/notes.md", first)
    assert [run(s.raw_text(DS, d["id"])) for d in run(s.list_data(DS))] == ["Likes horses."]


def test_write_file_checks_version(tmp_path):
    s = store(tmp_path)
    run(s.remember("Horses at the farm.", DS))
    f = s.read_file(DS, "personal/topics/notes.md")
    s.write_file(DS, f["file"], f["text"] + "- Likes jazz.\n", expected=f["version"])
    with pytest.raises(MemoryError_):
        s.write_file(DS, f["file"], f["text"], expected=f["version"])


def test_topics_and_resolve(tmp_path):
    s = store(tmp_path)
    run(s.remember("Name is Alex.", DS, metadata={"tags": ["kind-identity"]}))
    run(s.remember("Atlas launches in November.", DS, topic="Atlas"))
    t = {x["topic"]: x for x in s.topics(DS)}
    assert set(t) == {"profile", "atlas"} and t["atlas"]["title"] == "Atlas"
    assert s.resolve(DS, "atlas") == "personal/topics/atlas.md"
    assert route(s.repo, "personal", "News about [[Atlas]]") == "personal/topics/atlas.md"
    with pytest.raises(MemoryError_):
        s.resolve(DS, "nothing-here")


def test_paths_cannot_escape(tmp_path):
    r = MemoryRepo(tmp_path / "memory")
    for bad in ("../x.md", "personal/../../x.md", ".mnemos/index.md", "personal/a.txt"):
        with pytest.raises(MemoryError_):
            r.write_file(bad, "- x\n", "you")
    assert not (tmp_path / "x.md").exists()


def test_two_stores_share_the_repo(tmp_path):
    s1, s2 = store(tmp_path), store(tmp_path)
    a = run(s1.remember("Likes horses.", DS))["items"][0]["id"]
    b = run(s2.remember("Likes jazz.", DS))["items"][0]["id"]
    assert {d["id"] for d in run(s1.list_data(DS))} == {a, b}
    assert Path(tmp_path / "memory/.git").is_dir()
