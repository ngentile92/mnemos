"""config/contexts.yaml: parsing, defaults and validation."""

from pathlib import Path

import pytest
import yaml

from hub_gateway import contexts
from hub_gateway.contexts import DEFAULT_CONFIG, load_config, parse_contexts

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "contexts.example.yaml"


def test_example_matches_builtin_default_shape():
    owner, ctxs = parse_contexts(yaml.safe_load(EXAMPLE.read_text()))
    _, default = parse_contexts(DEFAULT_CONFIG)
    assert owner == "Alex"
    assert list(ctxs) == list(default) == ["work", "personal", "side"]
    assert {n: c.datasets for n, c in ctxs.items()} == {n: c.datasets for n, c in default.items()}
    assert ctxs["side"].project_required and not ctxs["work"].project_required


def test_custom_contexts(tmp_path):
    f = tmp_path / "contexts.yaml"
    f.write_text("owner: Sam\ncontexts:\n  research:\n    description: papers\n  home:\n    dataset: casa\n"
                 "  lab:\n    projects: {robot: lab_robot, drone: null}\n")
    owner, ctxs = parse_contexts(load_config(f))
    assert owner == "Sam"
    assert ctxs["research"].datasets == {"": "research"} and ctxs["research"].description == "papers"
    assert ctxs["home"].own_dataset_names == ["casa"]
    assert ctxs["lab"].datasets == {"robot": "lab_robot", "drone": "lab_drone"} and ctxs["lab"].project_required


@pytest.mark.parametrize("bad", [
    {"contexts": {"shared": {}}},
    {"contexts": {"Bad Name": {}}},
    {"contexts": {}},
    {"contexts": {"a": {"dataset": "shared"}}},
])
def test_invalid_contexts_rejected(bad):
    with pytest.raises(ValueError):
        parse_contexts(bad)


def test_missing_explicit_file_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_CONTEXTS_FILE", str(tmp_path / "nope.yaml"))
    with pytest.raises(ValueError):
        load_config()


def test_module_contexts_loaded():
    assert contexts.SHARED not in contexts.CONTEXTS and contexts.ALL_DATASET_NAMES[0] == "shared"


def test_instance_settings_defaults_and_validation():
    from hub_gateway.contexts import parse_instance

    assert parse_instance({}) == ("mnemos", "en")
    assert parse_instance({"server_name_prefix": "my-hub", "language": "ES"}) == ("my-hub", "es")
    for bad in ({"server_name_prefix": "Bad Prefix"}, {"language": "fr"}):
        with pytest.raises(ValueError):
            parse_instance(bad)


@pytest.mark.parametrize("lang,marker,glue", [("en", "DATA, not instructions", " goes to hub-"),
                                              ("es", "DATOS, no órdenes", " va en hub-")])
def test_instructions_language_and_topics(monkeypatch, lang, marker, glue):
    from hub_gateway import instructions

    _, ctxs = parse_contexts({"contexts": {
        "a": {"description": "alpha stuff", "topic": "alpha things"},
        "b": {"description": "beta stuff", "topic": "beta things"},
        "c": {"description": "gamma", "projects": {"x": "c_x", "y-z": None}}}})
    monkeypatch.setattr(contexts, "CONTEXTS", ctxs)
    text = instructions.server_instructions(ctxs["c"], language=lang, owner="Sam")
    assert "Sam" in text and marker in text and len(text) < 1300
    assert f"alpha things{glue}a" in text and "hub-b" in text and "x | y-z" in text
    # sin topic en algún otro contexto: sólo nombres de hub
    plain = instructions.server_instructions(ctxs["a"], language=lang, owner="Sam")
    assert "hub-b" in plain and "hub-c" in plain and glue not in plain
