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


# ---- v0.10.2: the second dry run's noise, synthetic
from hub_gateway.local_model import is_acronym, repeated_initial_names, strip_trailers  # noqa: E402

NOTES2 = [
    {"id": "a1", "text": "Acme deploys go through the staging gateway first. Acme customers get release notes weekly."},
    {"id": "a2", "text": "Fixed the login flow.\n\nCo-authored-by: Bot Helper <bot@example.com>"},
    {"id": "a3", "text": "Kestrelo is the side project for resale tickets; it needs OAuth before launch."},
    {"id": "a4", "text": "Kestrelo payments move to a new provider next month."},
    {"id": "a5", "text": "The app enables RLS on every table and uses ADC for cloud credentials."},
    {"id": "a6", "text": "Update the READMEs before shipping the Funnel setup for the project."},
    {"id": "a7", "text": "Thermacorp sent the quote for the boiler project at home."},
    {"id": "a8", "text": "My sister Marta lives in Valencia and works with Acme."},
]


def _dry(tmp_path, context="acme"):
    return Model(tmp_path / "m.sqlite").migrate(NOTES2, owner="Ana", context=context)


def test_person_needs_a_nearby_person_cue(tmp_path):
    ents = {e["name"]: e["kind"] for e in _dry(tmp_path)["entities"]}
    assert ents.get("Marta") == "person"
    for n in ("Thermacorp", "Kestrelo", "READMEs", "Acme"):
        assert ents.get(n) != "person", n


def test_repeated_sentence_initial_names_count(tmp_path):
    assert "Kestrelo" in repeated_initial_names(n["text"] for n in NOTES2)
    assert "Fixed" not in repeated_initial_names(["Fixed it.", "Fixed again. we fixed more"])
    ents = {e["name"]: e["kind"] for e in _dry(tmp_path)["entities"]}
    assert ents.get("Kestrelo") == "project"


def test_commit_trailers_are_ignored(tmp_path):
    assert "Co-authored-by" not in strip_trailers(NOTES2[1]["text"])
    names = {e["name"] for e in _dry(tmp_path)["entities"]}
    assert not any("authored" in n.lower() or "Bot Helper" == n for n in names)


def test_acronyms_and_tech_terms_are_not_projects_or_people(tmp_path):
    for a in ("PR", "ADC", "RLS", "OAuth", "READMEs"):
        assert is_acronym(a), a
    assert not is_acronym("Kestrelo") and not is_acronym("Laura")
    for e in _dry(tmp_path)["entities"]:
        if e["name"] in ("OAuth", "RLS", "ADC", "Funnel", "READMEs"):
            assert e["kind"] in ("topic", "tool"), e


def test_context_name_is_a_background_org(tmp_path):
    r = _dry(tmp_path)
    ents = {e["name"].lower(): e for e in r["entities"]}
    assert "acme" not in ents or (ents["acme"]["kind"] == "org" and ents["acme"]["facts"] == 0)
    # personal-style context names are not seeded as orgs
    assert all(e["name"].lower() != "personal" for e in _dry(tmp_path, "personal")["entities"])
