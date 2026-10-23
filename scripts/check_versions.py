#!/usr/bin/env python3
"""Fail if the version differs between the app, the .mcpb, npm, the Python package and the release notes.

usage: scripts/check_versions.py [--tag vX.Y.Z]
With --tag (release builds) the tag must match too and docs/release-notes.md must have a `## vX.Y.Z` section.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions():
    out = {}
    for rel in ("desktop/src-tauri/tauri.conf.json", "packaging/mcpb/manifest.json", "packaging/npm/package.json"):
        out[rel] = json.loads((ROOT / rel).read_text())["version"]
    m = re.search(r'^version = "([^"]+)"', (ROOT / "gateway/pyproject.toml").read_text(), re.M)
    out["gateway/pyproject.toml"] = m.group(1) if m else None
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "gateway/src/hub_gateway/__init__.py").read_text())
    out["gateway/src/hub_gateway/__init__.py"] = m.group(1) if m else None
    return out


def main(argv):
    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else None
    vs = versions()
    errors = []
    want = vs["desktop/src-tauri/tauri.conf.json"]
    for f, v in vs.items():
        if v != want:
            errors.append(f"{f}: {v} (expected {want})")
    notes = (ROOT / "docs/release-notes.md").read_text()
    if f"## v{want}\n" not in notes:
        errors.append(f"docs/release-notes.md: no '## v{want}' section")
    if tag and tag.lstrip("v") != want:
        errors.append(f"tag {tag} does not match version {want}")
    if errors:
        print("Version mismatch:\n  " + "\n  ".join(errors))
        return 1
    print(f"versions OK: {want}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
