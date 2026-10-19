"""Entity typing noise seen on a real dry run, reproduced with synthetic notes: tools typed as people/projects,
common capitalised words becoming entities, a tool named in passing becoming the subject, many 'unknown' kinds."""
from hub_gateway.local_model import Model, guess_kind, is_tool, name_candidates

NOTES = [
    ("n1", "Ana opens every PR on GitHub with a short summary first."),
    ("n2", "Ana wants the PR template on GitHub to include a test plan."),
    ("n3", "Ana tracks the sprint tasks in ClickUp and works with ClickUp daily."),
    ("n4", "Instrucciones: answer in Spanish and keep it short."),
    ("n5", "Inyectables are reviewed every Monday by the clinic team."),
    ("n6", "The home WiFi drops when the router overheats; Ana works with WiFi extenders."),
    ("n7", "Ana is building [[Ticketera]], a ticket resale app."),
    ("n8", "Ticketera runs on Tailscale for remote access and uses Claude for support replies."),
    ("n9", "Her manager is Laura Pérez."),
    ("n10", "PRs should be small and reviewed before merging."),
]


def _model(tmp_path):
    m = Model(tmp_path / "m.sqlite")
    m.me("Ana")
    for nid, text in NOTES:
        m.add_fact(nid, text)
    return m


def test_tools_are_typed_tool_and_never_people_or_projects(tmp_path):
    m = _model(tmp_path)
    ents = {e["name"]: e for e in m.entities()}
    for t in ("GitHub", "ClickUp", "WiFi", "Tailscale", "Claude"):
        assert t not in ents or ents[t]["kind"] == "tool", t
    assert ents["Ticketera"]["kind"] == "project" and ents["Laura Pérez"]["kind"] == "person"


def test_common_words_and_sentence_starts_are_not_entities(tmp_path):
    names = {e["name"] for e in _model(tmp_path).entities()}
    for w in ("PR", "PRs", "Instrucciones", "Inyectables"):
        assert w not in names, w
    assert name_candidates("Instrucciones: always reply in English.") == []
    assert name_candidates("Opened a PR for the API on Monday.") == []
    assert name_candidates("She met Laura Pérez at the office.") == ["Laura Pérez"]


def test_passing_tool_mentions_do_not_become_the_subject(tmp_path):
    m = _model(tmp_path)
    by = {f["note_id"]: f for f in m.facts()}
    for nid in ("n1", "n2", "n3", "n6"):
        assert by[nid]["entity_kind"] != "tool", nid
    assert by["n8"]["entity"] == "Ticketera"  # a known project named in the fact wins over the tools in it
    tool_facts = [f for f in m.facts() if f["entity_kind"] == "tool"]
    assert not tool_facts


def test_kind_guessing_covers_instructions_skills_and_state():
    assert guess_kind("PRs should be small and reviewed before merging.") == "preference"
    assert guess_kind("Instrucciones: no usar emojis en los mails.") == "preference"
    assert guess_kind("Maneja Kubernetes y Terraform en el trabajo.") == "skill_fact"
    assert guess_kind("The billing service is now deployed to production.") == "project_state"
    unknown = sum(guess_kind(t) is None for _, t in NOTES)
    assert unknown <= 2


def test_known_tools_are_configurable(monkeypatch):
    assert is_tool("GitHub") and not is_tool("Ticketera")
    monkeypatch.setenv("MNEMOS_KNOWN_TOOLS", "Ticketera, Foo Bar")
    assert is_tool("ticketera") and is_tool("Foo  Bar")
