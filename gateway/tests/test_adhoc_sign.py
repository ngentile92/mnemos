"""The Mac app must ship ad-hoc signed and sealed: an unsigned download shows "Mnemos is damaged"."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_tauri_adhoc_identity():
    conf = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
    assert conf["bundle"]["macOS"]["signingIdentity"] == "-"


def test_release_and_ci_sign_and_verify():
    for wf in ("release.yml", "ci.yml"):
        s = (ROOT / ".github/workflows" / wf).read_text()
        assert "desktop/scripts/adhoc_sign.sh" in s and "desktop/scripts/make_dmg.sh" in s, wf
        assert "codesign --verify --deep --strict" in s, wf
    sign = (ROOT / "desktop/scripts/adhoc_sign.sh").read_text()
    assert "codesign --force --sign -" in sign and "Mach-O" in sign


def test_install_docs_explain_first_open():
    d = (ROOT / "docs/INSTALL-mac.md").read_text()
    assert "Open Anyway" in d and "Abrir igualmente" in d and "0.17.1" in d
