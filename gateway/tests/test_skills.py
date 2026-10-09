import os

import pytest

from hub_gateway.skills import SkillError, SkillIndex, scan
from conftest import write_skill


def names(idx):
    return {s.name for s in idx.visible()}


def test_visibility_per_context(skills_repo):
    assert names(SkillIndex(skills_repo, "work")) == {"revisar-pr", "review-cliente", "propuesta-poc"}
    assert names(SkillIndex(skills_repo, "personal")) == {"revisar-pr", "rutina-gimnasio"}
    # propuesta-poc es de work pero se comparte con side vía metadata.hub-share
    assert names(SkillIndex(skills_repo, "side")) == {"revisar-pr", "shop-deploy", "propuesta-poc"}


def test_foreign_skill_is_indistinguishable_from_missing(skills_repo):
    idx = SkillIndex(skills_repo, "personal")
    with pytest.raises(SkillError, match="no encontrada"):
        idx.read("review-cliente")
    with pytest.raises(SkillError, match="no encontrada"):
        idx.read("no-existe")


def test_read_skill_and_reference_file(skills_repo):
    idx = SkillIndex(skills_repo, "work")
    assert "Cuerpo" in idx.read("review-cliente")
    assert idx.read("review-cliente", "references/checklist.md") == "- item"
    assert "references/checklist.md" in idx.files("review-cliente")


@pytest.mark.parametrize("bad", ["../rutina-gimnasio/SKILL.md", "../../personal/rutina-gimnasio/SKILL.md", "/etc/passwd"])
def test_path_traversal_blocked(skills_repo, bad):
    idx = SkillIndex(skills_repo, "work")
    with pytest.raises(SkillError):
        idx.read("review-cliente", bad)


def test_symlink_escape_blocked(skills_repo):
    target = skills_repo / "skills" / "personal" / "rutina-gimnasio" / "SKILL.md"
    link = skills_repo / "skills" / "shared" / "revisar-pr" / "leak.md"
    os.symlink(target, link)
    idx = SkillIndex(skills_repo, "work")
    with pytest.raises(SkillError, match="fuera"):
        idx.read("revisar-pr", "leak.md")


def test_invalid_skills_reported_not_served(skills_repo):
    write_skill(skills_repo, "personal", "Mal-Nombre")
    write_skill(skills_repo, "personal", "owner-mentira", metadata={"hub-owner": "work"})
    write_skill(skills_repo, "personal", "share-rara", metadata={"hub-share": "marte"})
    d = skills_repo / "skills" / "personal" / "carpeta-distinta"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: otro-nombre\ndescription: x\n---\n")
    skills, errors = scan(skills_repo)
    assert "owner-mentira" not in skills and "share-rara" not in skills and "otro-nombre" not in skills
    assert len(errors) == 4


def test_duplicate_names_are_dropped(skills_repo):
    write_skill(skills_repo, "personal", "revisar-pr")
    skills, errors = scan(skills_repo)
    assert "revisar-pr" not in skills
    assert any("duplicado" in e for e in errors)


def test_index_refreshes_on_change(skills_repo):
    idx = SkillIndex(skills_repo, "personal", min_interval=0)
    assert "nueva" not in names(idx)
    write_skill(skills_repo, "shared", "nueva")
    assert "nueva" in names(idx)


def test_missing_repo_is_empty(tmp_path):
    idx = SkillIndex(tmp_path / "nada", "personal")
    assert idx.visible() == []


# ---------------------------------------------------------------- gbrain-format skills

GBRAIN_SKILL = """---
name: query
version: 1.0.0
description: |
  Answer questions using the brain's knowledge.
  Use when the user asks a question.
triggers:
  - "what do we know about"
  - "who is"
tools:
  - search
  - get_page
  - put_page
  - exec
mutating: false
writes_pages: false
upstream: gbrain
---

# Query Skill
"""


def _gbrain_pack(root):
    d = root / "skills" / "shared" / "query"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(GBRAIN_SKILL, encoding="utf-8")
    (d / "routing-eval.jsonl").write_text('{"intent": "who is Ben"}\n', encoding="utf-8")
    (root / "skills" / "shared" / "RESOLVER.md").write_text("# Resolver\n", encoding="utf-8")
    (root / "skills" / "shared" / "_output-rules.md").write_text("# Rules\n", encoding="utf-8")
    (root / "skills" / "shared" / "conventions").mkdir()
    (root / "skills" / "shared" / "conventions" / "quality.md").write_text("# Q\n", encoding="utf-8")


def test_gbrain_skill_loads_with_routing_fields(skills_repo):
    _gbrain_pack(skills_repo)
    skills, errors = scan(skills_repo)
    s = skills["query"]
    assert errors == []
    assert s.description == "Answer questions using the brain's knowledge. Use when the user asks a question."
    assert s.triggers == ("what do we know about", "who is") and s.version == "1.0.0" and s.mutating is False
    assert s.summary()["triggers"] == ["what do we know about", "who is"]
    # existing Agent Skills keep the same minimal summary
    assert set(skills["revisar-pr"].summary()) == {"name", "description", "owner"}
    assert "routing-eval.jsonl" in SkillIndex(skills_repo, "work").files("query")


def test_gbrain_tool_equivalents():
    from hub_gateway.skills import tool_equivalents

    assert tool_equivalents(("search", "get_page", "put_page", "exec", "mcp:recall", "memory_save")) == {
        "search": "memory_search", "get_page": "memory_list", "put_page": "memory_save", "exec": None,
        "mcp:recall": "memory_search", "memory_save": "memory_save"}


@pytest.mark.parametrize("bad", ['triggers: "solo un texto"', "triggers:\n  - {a: 1}", "mutating: quizás",
                                 "tools: 3"])
def test_malformed_gbrain_fields_rejected(skills_repo, bad):
    d = skills_repo / "skills" / "shared" / "rota"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: rota\ndescription: x\n{bad}\n---\nbody\n", encoding="utf-8")
    skills, errors = scan(skills_repo)
    assert "rota" not in skills and any("rota" in e for e in errors)


def test_validator_accepts_gbrain_pack(skills_repo):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "validate", Path(__file__).resolve().parents[2] / "examples" / "skills" / "scripts" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _gbrain_pack(skills_repo)
    assert [e for e in mod.validate(skills_repo) if "query" in e or "conventions" in e] == []
