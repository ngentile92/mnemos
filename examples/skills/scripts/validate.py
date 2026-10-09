#!/usr/bin/env python3
"""Validates a Mnemos skills repo with the same rules the gateway applies (gateway/src/hub_gateway/skills.py).

Rules:
  * Layout: skills/<owner>/<name>/SKILL.md with owner in {shared, <contexts>}.
    Contexts default to "work personal side"; override with SKILLS_CONTEXTS="research home ..." to match
    your config/contexts.yaml.
  * YAML frontmatter with `name` and `description`; `name` == folder name, kebab-case, <= 64 chars.
  * `metadata` (optional) is a map; `metadata.hub-owner`, if present, matches the folder;
    `metadata.hub-share` is a space-separated list of valid contexts.
  * Unique names across the repo. No symlinks. Files <= 256 KiB.
  * gbrain-format skills are accepted: optional `triggers` / `tools` (lists of strings), `mutating` (bool),
    `version`; other keys are ignored. gbrain skillpack support files are allowed next to the skills:
    `_*` / `RESOLVER.md` files and `_*`, `conventions/`, `migrations/` folders (no SKILL.md needed).
  * Anti-secret heuristic: rejects typical token and private-key patterns.

Usage: python scripts/validate.py [root]   (requires PyYAML)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

CONTEXTS = tuple((os.environ.get("SKILLS_CONTEXTS") or "work personal side").split())
OWNERS = ("shared", *CONTEXTS)
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_FILE_BYTES = 256 * 1024
SECRET_RES = [
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{50,}\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{35}\b",
    )
]


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise ValueError("SKILL.md has no YAML frontmatter")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ValueError("frontmatter is not closed with '---'")
    data = yaml.safe_load(parts[0][3:]) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a map")
    return data


def check_skill(skill_md: Path, owner: str) -> str:
    data = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = str(data.get("name", "")).strip()
    desc = " ".join(str(data.get("description", "")).split())
    if not name or not desc:
        raise ValueError("missing name or description")
    if name != skill_md.parent.name:
        raise ValueError(f"name {name!r} does not match folder {skill_md.parent.name!r}")
    if not NAME_RE.match(name) or len(name) > 64:
        raise ValueError(f"invalid name {name!r} (kebab-case, <= 64)")
    if len(desc) > 1024:
        raise ValueError("description longer than 1024 characters")
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        raise ValueError("metadata must be a map")
    declared = meta.get("hub-owner")
    if declared and str(declared) != owner:
        raise ValueError(f"hub-owner={declared!r} but it lives in skills/{owner}/")
    share = set(str(meta.get("hub-share", "")).split())
    if share - set(CONTEXTS):
        raise ValueError(f"hub-share with unknown contexts: {sorted(share - set(CONTEXTS))}")
    for key in ("triggers", "tools"):
        val = data.get(key)
        if val is not None and (not isinstance(val, list) or not all(isinstance(v, (str, int, float)) for v in val)):
            raise ValueError(f"{key} must be a list of strings")
    if len(data.get("triggers") or []) > 50:
        raise ValueError("more than 50 triggers")
    if data.get("mutating") is not None and not isinstance(data.get("mutating"), bool):
        raise ValueError("mutating must be true or false")
    return name


SUPPORT_DIRS = {"conventions", "migrations"}


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    base = root / "skills"
    if not base.is_dir():
        return [f"{base} does not exist"]
    for entry in base.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name not in OWNERS:
            errors.append(f"{entry}: unknown folder (valid: {', '.join(OWNERS)})")
    seen: dict[str, Path] = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"{rel}: symlinks are not allowed")
            continue
        if path.is_file():
            if path.stat().st_size > MAX_FILE_BYTES:
                errors.append(f"{rel}: larger than {MAX_FILE_BYTES // 1024} KiB")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if any(r.search(text) for r in SECRET_RES):
                errors.append(f"{rel}: looks like it contains a secret")
    for owner in OWNERS:
        odir = base / owner
        if not odir.is_dir():
            continue
        for d in sorted(p for p in odir.iterdir() if p.is_dir() and not p.is_symlink()):
            skill_md = d / "SKILL.md"
            if not skill_md.is_file() and (d.name.startswith("_") or d.name in SUPPORT_DIRS):
                continue  # gbrain skillpack support folder
            if not skill_md.is_file():
                errors.append(f"{d.relative_to(root)}: missing SKILL.md")
                continue
            try:
                name = check_skill(skill_md, owner)
            except (ValueError, yaml.YAMLError) as exc:
                errors.append(f"{skill_md.relative_to(root)}: {exc}")
                continue
            if name in seen:
                errors.append(f"duplicate name {name!r}: {seen[name].relative_to(root)} and {d.relative_to(root)}")
            seen[name] = d
    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
    errors = validate(root)
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        return 1
    print(f"OK: valid skills in {root / 'skills'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
