import asyncio

from hub_gateway import local_route as lr

SP = [{"name": "personal", "label": "Personal", "description": ""}, {"name": "work", "label": "Work", "description": ""}]


def run(c):
    return asyncio.run(c)


def test_rules_by_content_and_default_descriptions():
    d = {s["name"]: s for s in lr.describe(SP)}
    assert "family" in d["personal"]["description"].lower() and not d["work"]["custom"]
    r = lr.by_rules("Sprint review with the client and the backend team on Monday", SP, "personal")
    assert r["space"] == "work" and not r["review"]
    r = lr.by_rules("Mi perra Lola y mi pareja vamos de viaje en vacaciones", SP, "work")
    assert r["space"] == "personal"
    r = lr.by_rules("Likes blue", SP, "personal")
    assert r["space"] == "personal" and r["review"]


def test_user_description_wins_over_generic_hints():
    sp = [{"name": "personal", "description": "home, family"}, {"name": "atlas", "label": "Atlas", "description": "the Atlas data platform: dbt, BigQuery"}]
    assert lr.by_rules("Moved the dbt models to BigQuery", sp, "personal")["space"] == "atlas"


def test_folder_mapping_overrides_content(tmp_path):
    lr.set_folder(tmp_path, str(tmp_path / "code" / "site"), "personal", ["personal", "work"])
    r = run(lr.route("Deploy the backend to production for the client", SP, "work", home=tmp_path,
                     cwd=str(tmp_path / "code" / "site" / "src")))
    assert r["space"] == "personal" and r["by"] == "folder"
    assert run(lr.route("x", SP, "work", home=tmp_path, cwd=str(tmp_path / "other")))["by"] != "folder"
    lr.set_folder(tmp_path, str(tmp_path / "code" / "site"), "", ["personal", "work"])
    assert lr.folders(tmp_path) == []


def test_explicit_wins_and_model_low_confidence_goes_to_default():
    class L:
        async def json(self, p):
            return {"r": [{"n": 0, "space": "work", "confidence": 0.9, "reason": "trabajo"},
                          {"n": 1, "space": "work", "confidence": 0.3, "reason": "maybe"}]}
    rs = run(lr.route_many(["a", "b"], SP, "personal", llm=L()))
    assert rs[0]["space"] == "work" and rs[0]["by"] == "model"
    assert rs[1]["space"] == "personal" and rs[1]["review"] and rs[1]["suggested"] == "work"
    assert run(lr.route("a", SP, "personal", llm=L(), explicit="personal"))["by"] == "explicit"
