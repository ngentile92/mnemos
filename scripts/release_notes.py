#!/usr/bin/env python3
"""Print the GitHub Release body for one version: the intro of docs/release-notes.md + its `## vX.Y.Z` section.

usage: scripts/release_notes.py v0.14.0 > body.md
"""
import sys
from pathlib import Path

NOTES = Path(__file__).resolve().parent.parent / "docs/release-notes.md"


def body(tag: str, text: str) -> str:
    v = "v" + tag.lstrip("v")
    intro = text.split("\n## ", 1)[0]
    intro = "\n".join(l for l in intro.splitlines() if not l.startswith("Newest first.")).rstrip()
    start = text.find(f"\n## {v}\n")
    if start < 0:
        return intro + "\n"
    rest = text[start + 1:]
    end = rest.find("\n## ", 1)
    section = rest if end < 0 else rest[:end]
    return intro + "\n\n" + section.strip() + "\n\nAll versions: [docs/release-notes.md](https://github.com/ngentile92/mnemos/blob/main/docs/release-notes.md)\n"


if __name__ == "__main__":
    sys.stdout.write(body(sys.argv[1], NOTES.read_text()))
