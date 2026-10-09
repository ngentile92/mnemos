"""The example skills repo loads cleanly with the gateway's own loader."""
from pathlib import Path

from hub_gateway.skills import scan

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/skills"


def test_example_skills_all_load():
    skills, errors = scan(EXAMPLES)
    assert errors == []
    assert {"review-pr", "debug-error", "status-report", "technical-docs", "memory-capture", "meeting-notes",
            "weekly-review", "incident-postmortem", "release-notes"} <= set(skills)


def test_example_skills_owners_and_gbrain_fields():
    skills, _ = scan(EXAMPLES)
    assert skills["weekly-review"].owner == "personal"
    assert skills["incident-postmortem"].owner == "work"
    assert skills["release-notes"].owner == "side"
    assert skills["memory-capture"].triggers and skills["memory-capture"].mutating is True
    assert skills["memory-capture"].visible_in("work") and not skills["weekly-review"].visible_in("work")
