"""scripts/mnemos_context.py: add/remove contexts touching only files (contexts.yaml, .env, compose.generated.yaml)."""
import importlib.util
import shutil
import stat
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mnemos_context", ROOT / "scripts" / "mnemos_context.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


@pytest.fixture
def inst(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    shutil.copy(ROOT / "config" / "contexts.example.yaml", tmp_path / "config" / "contexts.example.yaml")
    (tmp_path / ".env").write_text("GITHUB_USER=alex\nTS_TAILNET=tail123\nGH_OAUTH_WORK_ID=keepme\n")
    return tmp_path


def run(inst, *args):
    return mc.main(["--root", str(inst), *args])


def test_add_context_writes_config_env_and_compose(inst, capsys):
    assert run(inst, "add", "research", "--description", "papers") == 0
    cfg = yaml.safe_load((inst / "config/contexts.yaml").read_text())
    assert cfg["contexts"]["research"] == {"description": "papers", "dataset": "research"}
    assert set(cfg["contexts"]) == {"work", "personal", "side", "research"}
    env = (inst / ".env").read_text()
    assert env.startswith("GITHUB_USER=alex\nTS_TAILNET=tail123\nGH_OAUTH_WORK_ID=keepme\n")
    keys = mc.env_keys(env)
    assert {"COMPOSE_FILE", "GH_OAUTH_RESEARCH_ID", "HUB_JWT_SIGNING_KEY_RESEARCH", "COGNEE_PW_RESEARCH"} <= keys
    assert "HUB_STORAGE_KEY_RESEARCH=__" not in env and "GH_OAUTH_RESEARCH_ID=__COMPLETAR__" in env
    assert stat.S_IMODE((inst / ".env").stat().st_mode) == 0o600
    compose = yaml.safe_load((inst / "compose.generated.yaml").read_text())
    assert {"ts-research", "gateway-research", "gateway-work"} <= set(compose["services"])
    out = capsys.readouterr().out
    assert "https://hub-research.tail123.ts.net/auth/callback" in out
    assert list(inst.glob(".env.bak-*")) and list((inst / "config").glob("contexts.yaml.bak-*"))


def test_add_with_projects_and_dry_run(inst):
    assert run(inst, "add", "lab", "--project", "robot", "--project", "drone", "--dry-run") == 0
    assert not (inst / "compose.generated.yaml").exists() and "LAB" not in (inst / ".env").read_text()
    assert run(inst, "add", "lab", "--project", "robot", "--project", "drone") == 0
    cfg = yaml.safe_load((inst / "config/contexts.yaml").read_text())
    assert cfg["contexts"]["lab"]["projects"] == {"robot": "lab_robot", "drone": "lab_drone"}


@pytest.mark.parametrize("name", ["work", "shared", "Bad", "1x"])
def test_add_rejects_bad_or_existing(inst, name):
    with pytest.raises(SystemExit):
        run(inst, "add", name)


def test_add_twice_does_not_duplicate_env(inst):
    run(inst, "add", "research")
    run(inst, "remove", "research", "--yes")
    run(inst, "add", "research")
    env = (inst / ".env").read_text()
    assert env.count("GH_OAUTH_RESEARCH_ID=") == 1 and env.count("COMPOSE_FILE=") == 1


def test_remove_needs_yes_drops_bridges_and_keeps_env(inst):
    run(inst, "add", "research")
    cf = inst / "config/contexts.yaml"
    cfg = yaml.safe_load(cf.read_text())
    cfg["bridges"] = [{"from": "research", "to": "work"}, {"from": "side", "to": "work", "datasets": ["side_blog"]}]
    cf.write_text(yaml.safe_dump(cfg))
    assert run(inst, "remove", "research") == 0
    assert "research" in yaml.safe_load(cf.read_text())["contexts"]
    assert run(inst, "remove", "research", "--yes") == 0
    cfg = yaml.safe_load(cf.read_text())
    assert "research" not in cfg["contexts"] and cfg["bridges"] == [{"from": "side", "to": "work", "datasets": ["side_blog"]}]
    assert "gateway-research" not in yaml.safe_load((inst / "compose.generated.yaml").read_text())["services"]
    assert "GH_OAUTH_RESEARCH_ID" in (inst / ".env").read_text()


def test_cannot_remove_last_or_unknown(inst):
    with pytest.raises(SystemExit):
        run(inst, "remove", "nope", "--yes")
    run(inst, "remove", "personal", "--yes")
    run(inst, "remove", "side", "--yes")
    with pytest.raises(SystemExit):
        run(inst, "remove", "work", "--yes")


def test_list(inst, capsys):
    run(inst, "add", "research")
    capsys.readouterr()
    assert run(inst, "list") == 0
    out = capsys.readouterr().out
    assert "research" in out and "GH_OAUTH_RESEARCH_ID" in out
