import importlib.util, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_versions_match():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_versions.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_release_body_has_only_its_section():
    rn = _load("release_notes")
    text = "Intro line\n\nNewest first. x\n\n## v1.1.0\n\nnew stuff\n\n## v1.0.0\n\nold stuff\n"
    b = rn.body("v1.1.0", text)
    assert "Intro line" in b and "new stuff" in b and "old stuff" not in b and "Newest first" not in b
    assert "old stuff" in rn.body("1.0.0", text)
