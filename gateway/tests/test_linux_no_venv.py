"""Linux hosts without a Python venv: scripts fall back to the gateway image; the dashboard copes without docker."""
import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("memory_client", ROOT / "dashboard" / "memory_client.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _repo_copy(tmp_path: Path, script: str) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / script).write_text((ROOT / "scripts" / script).read_text())
    return tmp_path


def test_cognify_nightly_runs_in_gateway_image_without_venv(tmp_path):
    (tmp_path / "repo").mkdir()
    repo = _repo_copy(tmp_path / "repo", "cognify_nightly.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    _exe(bin_dir / "docker", f'echo "$@" >> {log}\n')
    _exe(bin_dir / "curl", "echo 200\n")
    _exe(bin_dir / "pgrep", "exit 1\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
           "MNEMOS_COGNIFY_GRACE": "0", "MNEMOS_HYGIENE": "report"}
    r = subprocess.run(["bash", str(repo / "scripts" / "cognify_nightly.sh")], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    runs = [line for line in log.read_text().splitlines() if line.startswith("run ")]
    assert len(runs) == 2
    assert "--network host" in runs[0] and runs[0].endswith("mnemos/gateway:local -u scripts/cognify_pending.py --timeout 5400")
    assert runs[1].endswith("-u scripts/memory_hygiene.py")
    assert (tmp_path / "home" / "mnemos-hygiene").is_dir()


@pytest.mark.parametrize("only, needs_venv", [("watchdog", True), ("dashboard", True), ("backup,cognify", False)])
def test_systemd_units_need_venv_only_for_host_python(tmp_path, only, needs_venv):
    repo = _repo_copy(tmp_path, "install_systemd_units.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "systemctl", "exit 0\n")  # never touch the real user's units
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    r = subprocess.run(["bash", str(repo / "scripts" / "install_systemd_units.sh"), "--only", only,
                        "--dir", str(tmp_path / "units")], env=env, capture_output=True, text=True, timeout=60)
    assert ("missing venv" in r.stderr) is needs_venv
    assert (r.returncode == 0) is not needs_venv


def test_discover_url_without_docker_cli(monkeypatch):
    def no_docker(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(mc.subprocess, "run", no_docker)
    with pytest.raises(mc.MemoryEditorOff, match="MNEMOS_INTERNAL_URL_MY_CTX"):
        mc.discover_url(ROOT, {}, "my-ctx")
    assert mc.discover_url(ROOT, {"MNEMOS_INTERNAL_URL_MY_CTX": "http://x:1/mcp"}, "my-ctx") == "http://x:1/mcp"
