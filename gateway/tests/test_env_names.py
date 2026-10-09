"""MNEMOS_* env names win; legacy AIHUB_* names keep working."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sh(expr: str, env: dict[str, str]) -> str:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("MNEMOS_", "AIHUB_"))}
    return subprocess.run(["bash", "-c", f'echo "{expr}"'], env={**clean, **env},
                          capture_output=True, text=True, check=True).stdout.strip()


def test_shell_fallback_order():
    e = "${MNEMOS_HYGIENE:-${AIHUB_HYGIENE:-report}}"
    assert e in (ROOT / "scripts/cognify_nightly.sh").read_text()
    assert _sh(e, {}) == "report"
    assert _sh(e, {"AIHUB_HYGIENE": "llm"}) == "llm"
    assert _sh(e, {"AIHUB_HYGIENE": "llm", "MNEMOS_HYGIENE": "off"}) == "off"


def test_shell_scripts_read_mnemos_first():
    for p in (ROOT / "scripts").glob("*.sh"):
        for line in p.read_text().splitlines():
            if "${AIHUB_" in line:
                assert "${MNEMOS_" in line, (p.name, line)


def test_dashboard_setting_fallback(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dash", ROOT / "dashboard/server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.delenv("MNEMOS_LABEL_PREFIX", raising=False)
    monkeypatch.delenv("AIHUB_LABEL_PREFIX", raising=False)
    (tmp_path / ".env").write_text("AIHUB_LABEL_PREFIX=com.old  # x\n")
    assert mod._setting("MNEMOS_LABEL_PREFIX", "io.mnemos") == "com.old"
    (tmp_path / ".env").write_text("AIHUB_LABEL_PREFIX=com.old\nMNEMOS_LABEL_PREFIX=com.new\n")
    assert mod._setting("MNEMOS_LABEL_PREFIX", "io.mnemos") == "com.new"
    monkeypatch.setenv("AIHUB_LABEL_PREFIX", "com.env")
    assert mod._setting("MNEMOS_LABEL_PREFIX", "io.mnemos") == "com.env"
