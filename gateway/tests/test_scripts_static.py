"""Scripts are not imported by the unit tests, so catch undefined names (e.g. a missing import) statically."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pyflakes = pytest.importorskip("pyflakes")


def test_no_undefined_names_in_scripts():
    files = [str(p) for p in sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "dashboard").glob("*.py"))]
    r = subprocess.run([sys.executable, "-m", "pyflakes", *files], capture_output=True, text=True)
    bad = [line for line in r.stdout.splitlines() if "undefined name" in line]
    assert not bad, "\n".join(bad)
