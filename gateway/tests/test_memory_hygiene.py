"""Tests de scripts/memory_hygiene.py: detecta, propone y solo borra duplicados exactos."""

import datetime as dt
import importlib.util
import json
import stat
from pathlib import Path

import httpx
import pytest
import respx

from conftest import DATASETS

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "memory_hygiene.py"
spec = importlib.util.spec_from_file_location("memory_hygiene", SCRIPT)
mh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mh)
BASE = "http://127.0.0.1:8010"


def note(i, ds, text, day="2026-10-01", tags=("x",), app="Cursor"):
    return {"id": i, "dataset": ds, "created_at": f"{day}T10:00:00", "tags": list(tags), "source_app": app, "text": text}


PR = "Billing foundation PR https://github.com/example-org/repo/pull/61 head {h} on dev/billing-foundation."
NOTES = [
    note("a1", "personal", "Alex vive en Málaga y usa una bici urbana.", tags=()),
    note("a2", "personal", "alex vive en  MALAGA y usa una bici urbana", day="2026-10-02", tags=("perfil",)),
    note("b1", "side_blog", "Blog usa Orion (MIT) como git submodule y plataforma base del appliance."),
    note("b2", "side_blog", "Blog usa Orion (MIT) como git submodule y plataforma base del appliance hispano."),
    note("c1", "work", PR.format(h="029bd23") + " dbt parse passed.", day="2026-10-02"),
    note("c2", "work", PR.format(h="15f8019") + " Sandbox v5 fix.", day="2026-10-03"),
    note("c3", "work", "2026-10-04 billing round 10, PR #61 still draft, head 604c666.", day="2026-10-04"),
    note("d1", "personal", "Alex vive en Málaga y usa una bici urbana.", tags=(), app=None),  # copia exacta de a1
    note("s1", "shared", "Token de prueba ghp_" + "A" * 36 + " no debería estar acá."),
]


def test_normalize_ignores_case_accents_punctuation():
    assert mh.normalize("Málaga,  MALAGA!") == "malaga malaga"


def test_analyze_finds_each_kind():
    res = mh.analyze(NOTES)
    ex = res["exact_duplicates"]
    assert len(ex) == 1 and ex[0]["dataset"] == "personal"
    assert ex[0]["keep"] == "a2" and sorted(ex[0]["delete"]) == ["a1", "d1"]   # más tags gana
    assert [(p["a"], p["b"]) for p in res["near_duplicates"]] == [("b1", "b2")]
    s = res["series"]
    assert len(s) == 1 and s[0]["anchor"] == "pr:61" and s[0]["newest"] == "c3" and s[0]["older"] == ["c1", "c2"]
    assert res["secret_like"] == ["s1"]
    assert "a1" in res["untagged"] and res["no_source_app"] == ["d1"]


def test_exact_duplicates_never_cross_datasets():
    res = mh.analyze([note("x", "personal", "mismo texto acá"), note("y", "shared", "mismo texto acá")])
    assert res["exact_duplicates"] == []


def test_render_never_prints_secret_value():
    md = mh.render(NOTES, mh.analyze(NOTES), today=dt.date(2026, 10, 5))
    assert "ghp_" not in md.split("## Metadatos")[1]
    assert "con forma de secreto (1): `s1`" in md and "total: 9" in md


@pytest.mark.parametrize("url", ["http://cognee:8000", "https://hub-side.tail.ts.net", "http://10.0.0.2:8010"])
def test_refuses_non_local(url):
    with pytest.raises(SystemExit):
        mh.ensure_local(url)


@respx.mock
def test_apply_deletes_only_exact_duplicates_with_owner(monkeypatch):
    logins = []

    def do_login(req):
        logins.append(req.content.decode())
        return httpx.Response(200, json={"access_token": "t"})
    respx.post(f"{BASE}/api/v1/auth/login").mock(side_effect=do_login)
    dele = respx.delete(url__regex=rf"{BASE}/api/v1/datasets/.+/data/.+").mock(return_value=httpx.Response(200))
    env = {"COGNEE_PW_PERSONAL": "pw-p", "COGNEE_PW_ADMIN": "pw-a"}
    res = mh.analyze(NOTES)
    done = mh.apply_exact(BASE, env, DATASETS, res["exact_duplicates"])
    assert sorted(done) == ["a1", "d1"]
    assert dele.call_count == 2
    assert all(DATASETS["personal"] in str(c.request.url) for c in dele.calls)
    assert len(logins) == 1 and "ctx-personal" in logins[0]


@respx.mock
def test_fetch_notes_reads_metadata_and_text():
    ds = DATASETS["side_blog"]
    respx.get(url__regex=rf"{BASE}/api/v1/datasets/(?!{ds}).+/data$").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/api/v1/datasets/{ds}/data").mock(return_value=httpx.Response(200, json=[
        {"id": "n1", "createdAt": "2026-09-30T13:35:33", "externalMetadata": [{"tags": ["blog"], "source_app": "Cursor"}]}]))
    respx.get(f"{BASE}/api/v1/datasets/{ds}/data/n1/raw").mock(return_value=httpx.Response(200, content="Blog ✓".encode()))
    with httpx.Client(base_url=BASE) as c:
        notes = mh.fetch_notes(c, DATASETS)
    assert notes == [{"id": "n1", "dataset": "side_blog", "created_at": "2026-09-30T13:35:33", "tags": ["blog"],
                      "source_app": "Cursor", "project": None, "text": "Blog ✓"}]


def test_write_private_is_600(tmp_path):
    p = tmp_path / "h" / "r.md"
    mh.write_private(p, "x")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


@respx.mock
def test_classify_pairs_parses_ollama_json():
    respx.post("http://127.0.0.1:11434/api/chat").mock(return_value=httpx.Response(200, json={
        "message": {"content": json.dumps({"verdict": "superada", "reason": "c3 es la ronda más nueva"})}}))
    out = mh.classify_pairs(NOTES, [{"a": "c1", "b": "c3"}], "llama3.1:8b")
    assert out == {"c1|c3": {"verdict": "superada", "reason": "c3 es la ronda más nueva"}}
