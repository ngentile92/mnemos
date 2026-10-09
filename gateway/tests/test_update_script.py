"""Tests de scripts/update.sh: checkout + build + up + smoke, con rollback automático (docker simulado)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="sin git")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "inst"
    (r / "scripts").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    shutil.copy(SCRIPT, r / "scripts" / "update.sh")
    (r / ".gitignore").write_text(".env\ndev/\n")
    (r / ".env").write_text("SOMETHING=1\n")  # config real: gitignored, update.sh no la toca
    for v in ("1", "2", "3"):
        (r / "version").write_text(v)
        git(r, "add", "-A")
        git(r, "commit", "-qm", f"v{v}")
        git(r, "tag", f"v0.{v}.0")
    git(r, "checkout", "-q", "--detach", "v0.1.0")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text('#!/bin/sh\necho "$* @$(cat version)" >> "$DOCKER_LOG"\n'
                                    '[ "$1 $2" = "compose build" ] && [ "$(cat version)" = "$FAIL_BUILD_AT" ] && exit 1\nexit 0\n')
    (bin_dir / "docker").chmod(0o755)
    return r, bin_dir, tmp_path / "docker.log"


def run(repo, *args, smoke='[ "$(cat version)" != 3 ]', fail_build_at=""):
    r, bin_dir, log = repo
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_LOG": str(log),
                        "FAIL_BUILD_AT": fail_build_at, "MNEMOS_SKIP_FETCH": "1", "MNEMOS_SMOKE_CMD": smoke,
                        "MNEMOS_SMOKE_TRIES": "2", "MNEMOS_SMOKE_WAIT": "0"}
    p = subprocess.run(["bash", "scripts/update.sh", *args], cwd=r, env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return p, calls, (r / "version").read_text()


def test_update_to_tag(repo):
    p, calls, version = run(repo, "v0.2.0")
    assert p.returncode == 0, p.stdout + p.stderr
    assert version == "2" and calls == ["compose build @2", "compose up -d @2"]
    assert (repo[0] / ".env").read_text() == "SOMETHING=1\n"
    assert "v0.2.0" in (repo[0] / "dev" / "state" / "updates.log").read_text()


def test_failed_smoke_rolls_back(repo):
    run(repo, "v0.2.0")
    repo[2].unlink()
    p, calls, version = run(repo)  # sin ref: el último tag (v0.3.0), cuyo smoke falla
    assert p.returncode == 1 and "rollback OK" in p.stdout
    assert version == "2"
    assert calls == ["compose build @3", "compose up -d @3", "compose build @2", "compose up -d @2"]


def test_failed_build_rolls_back_without_up(repo):
    p, calls, version = run(repo, "v0.2.0", fail_build_at="2")
    assert p.returncode == 1 and version == "1"
    assert calls == ["compose build @2", "compose build @1", "compose up -d @1"]


def test_rollback_returns_to_branch(repo):
    git(repo[0], "checkout", "-q", "main")
    git(repo[0], "reset", "-q", "--hard", "v0.2.0")
    p, _, version = run(repo, "v0.3.0")
    assert p.returncode == 1 and version == "2"
    assert git(repo[0], "symbolic-ref", "--short", "HEAD") == "main"


def test_dirty_tree_and_dry_run_change_nothing(repo):
    (repo[0] / "version").write_text("local edit")
    p, calls, _ = run(repo, "v0.2.0")
    assert p.returncode == 1 and "sin commitear" in p.stdout and calls == []
    git(repo[0], "checkout", "-q", "--", "version")
    p, calls, version = run(repo, "--dry-run", "v0.3.0")
    assert p.returncode == 0 and version == "1" and calls == []


def test_unknown_ref(repo):
    p, calls, _ = run(repo, "v9.9.9")
    assert p.returncode == 1 and "ref desconocido" in p.stdout and calls == []
