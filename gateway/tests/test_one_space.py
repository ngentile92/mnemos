"""0.19 F: a new memory is ONE space the user can name; more spaces are added from the app, and existing homes with
several spaces keep working unchanged."""
from __future__ import annotations

import pytest
import yaml

from hub_gateway import contexts as _c, local, local_spaces as lsp, local_welcome


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    import os
    from pathlib import Path
    monkeypatch.setenv("HUB_CONTEXTS_FILE", os.environ["HUB_CONTEXTS_FILE"])  # local._env changes it
    yield
    _c.reload(Path(__file__).resolve().parents[2] / "config" / "contexts.example.yaml")  # what other tests use


def test_new_home_is_one_space(tmp_path):
    h = tmp_path / "m"
    assert local.main(["init", "--home", str(h), "--owner", "Ana"]) in (0, None)
    data = yaml.safe_load((h / "contexts.yaml").read_text())
    assert list(data["contexts"]) == ["main"]
    sp = lsp.read(h)["spaces"]
    assert [(s["name"], s["label"]) for s in sp] == [("main", "My memory")]
    _c.reload(h / "contexts.yaml")
    assert list(local.build_all(h, 0)) == ["main"]  # no Shared, no Personal/Work


def test_welcome_names_the_memory(tmp_path):
    h = tmp_path / "m"
    local.init_home(h, ["main"], "Ana")
    local_welcome.finish(h, "Ana", "Casa")
    assert lsp.read(h)["spaces"][0]["label"] == "Casa"
    with pytest.raises(ValueError):
        local_welcome.finish(h, "Ana", "x" * 41)


def test_welcome_never_renames_when_there_are_several_spaces(tmp_path):
    h = tmp_path / "m"
    local.init_home(h, ["personal", "work"], "Ana")
    local_welcome.finish(h, "Ana", "Casa")
    assert [s["label"] for s in lsp.read(h)["spaces"]] == ["Personal", "Work"]


def test_add_space_and_use_it_at_once(tmp_path):
    h = tmp_path / "m"
    local.init_home(h, ["main"], "Ana")
    _c.reload(h / "contexts.yaml")
    servers = local.build_all(h, 0)
    router = local.build_router(h, servers)
    key, out = lsp.add_space(h, "Trabajo en Acme", "my job at Acme: clients and team")
    assert key == "trabajoenacme"
    local.init_home(h, [key])
    router.router.reload_now()
    assert key in servers and key in router.router.apps.contexts
    assert (h / "data" / key).is_dir()
    assert [s["label"] for s in out["spaces"]] == ["My memory", "Trabajo en Acme"]
    assert yaml.safe_load((h / "contexts.yaml").read_text())["owner"] == "Ana"  # nothing else touched
    with pytest.raises(ValueError):
        lsp.add_space(h, "trabajo en acme")       # same name
    k2, _ = lsp.add_space(h, "¡Ñandú!")           # accents and symbols → a valid key
    assert k2 == "nandu"
    k3, _ = lsp.add_space(h, "Shared")            # never collides with the special Shared space
    assert k3 == "sharedspace"


def test_existing_two_space_home_is_unchanged(tmp_path):
    h = tmp_path / "m"
    local.init_home(h, ["personal", "work"], "Ana")
    before = (h / "contexts.yaml").read_text()
    local.init_home(h, ["main"])                  # e.g. a later init: contexts.yaml is never rewritten
    assert (h / "contexts.yaml").read_text() == before
    _c.reload(h / "contexts.yaml")
    assert sorted(local.build_all(h, 0)) == ["personal", "work"]
