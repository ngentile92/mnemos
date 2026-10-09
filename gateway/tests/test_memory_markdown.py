"""Tests de scripts/memory_markdown.py: formato de páginas gbrain, ida y vuelta, plan de import."""

import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "memory_markdown.py"
spec = importlib.util.spec_from_file_location("memory_markdown", SCRIPT)
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

ITEM = {"id": "1630db70-a5cb-4a4b-8c9f-2a9d065d7fcb", "createdAt": "2026-10-09T19:49:36.1",
        "externalMetadata": {"source_app": "claude-ai", "hub_context": "personal", "tags": ["gente"],
                             "saved_at": "2026-10-09T19:49:36+00:00"}}
TEXT = "Ana Pérez es CTO de [[Acme]].\nVive en Córdoba."


def test_export_is_gbrain_page():
    rel, content = mm.to_markdown(ITEM, TEXT, "personal")
    assert rel == "personal/2026-10-09-ana-perez-es-cto-de-acme-1630db70.md"
    fm = yaml.safe_load(content.split("\n---", 1)[0][3:])
    assert fm["type"] == "note" and fm["title"] == "Ana Pérez es CTO de [[Acme]]."
    assert fm["date"] == "2026-10-09T19:49:36Z" and fm["tags"] == ["gente"]
    assert fm["mnemos"] == {"id": ITEM["id"], "dataset": "personal", "source_app": "claude-ai",
                            "context": "personal", "saved_at": "2026-10-09T19:49:36+00:00"}
    assert content.endswith(TEXT + "\n")


def test_round_trip_keeps_text():
    _, content = mm.to_markdown(ITEM, TEXT, "personal")
    fm, text = mm.from_markdown(content)
    assert text == TEXT and fm["tags"] == ["gente"]


def test_reads_gbrain_export_with_title_and_timeline():
    page = ("---\ntype: person\ntitle: Ana Pérez\ntags:\n  - acme\n---\n\nAna es CTO.\n\n<!-- timeline -->\n\n"
            "- 2026-10-01: reunión\n")
    fm, text = mm.from_markdown(page)
    assert text.startswith("# Ana Pérez\n\nAna es CTO.") and "Timeline:" in text and "reunión" in text
    _, text2 = mm.from_markdown("---\ntitle: Atlas\n---\n# Atlas\nProyecto.\n")
    assert text2 == "# Atlas\nProyecto."  # title already the heading: not duplicated
    assert mm.from_markdown("sin frontmatter")[1] == "sin frontmatter"


def test_import_plan_skips_known_empty_hidden(tmp_path):
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "ana.md").write_text("---\ntitle: Ana Pérez\ntags: [a, b, c, d, e, f]\ndate: 2026-10-01\n---\nEs CTO.\n")
    (tmp_path / "dup.md").write_text("ya está")
    (tmp_path / "empty.md").write_text("---\ntitle: \n---\n")
    (tmp_path / ".gbrain").mkdir()
    (tmp_path / ".gbrain" / "x.md").write_text("interno")
    known = {mm.hashlib.sha256(b"ya est\xc3\xa1").hexdigest()}
    plan = {p["file"]: p for p in mm.import_plan(tmp_path, known)}
    assert set(plan) == {"people/ana.md", "dup.md", "empty.md"}
    assert plan["dup.md"]["skip"] == "already in the dataset" and plan["empty.md"]["skip"] == "empty"
    ana = plan["people/ana.md"]
    assert ana["tags"] == ["a", "b", "c", "d", "e"] and ana["metadata"]["source_app"] == "import:markdown"
    assert ana["metadata"]["original_date"] == "2026-10-01" and ana["text"] == "# Ana Pérez\n\nEs CTO."


def test_round_trip_long_first_line_no_heading_added():
    long = "Carrera de Nicolás (hasta ago 2026): rechazó una oferta de Senior Applied Scientist en otra empresa grande"
    _, content = mm.to_markdown(ITEM, long, "personal")
    assert mm.from_markdown(content)[1] == long
