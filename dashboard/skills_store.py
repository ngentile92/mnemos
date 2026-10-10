"""Skills in a local folder (default for new installs): read, create and edit from the dashboard.

Mode is decided by SKILLS_DIR in .env:
  - SKILLS_DIR set (e.g. ./skills-local): local folder, mounted read-only into the gateways; the dashboard edits it.
  - unset: GitHub mode (skills-sync clones SKILLS_REPO); the dashboard only reads, edits go through the repo.

Layout is the same in both modes: <root>/skills/<owner>/<name>/SKILL.md (owner = shared or a context).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from hub_gateway.skills import MAX_FILE_BYTES, NAME_RE, OWNERS, SkillError, load_skill, parse_frontmatter, scan

TEMPLATE = """---
name: {name}
description: {description}
---

# {name}

When to use it, and the steps to follow.
"""


def local_root(repo_root: Path, env: dict[str, str]) -> Path | None:
    raw = (os.environ.get("SKILLS_DIR") or env.get("SKILLS_DIR") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else repo_root / p).resolve()


def ensure_layout(root: Path, owners=OWNERS) -> None:
    for o in owners:
        (root / "skills" / o).mkdir(parents=True, exist_ok=True)


def save(root: Path, owner: str, content: str, edit: str | None = None) -> dict:
    """Validates SKILL.md with the gateway's own loader, then writes it atomically.
    edit=None → create (fails if the name exists anywhere); edit=<name> → overwrite that skill (no renames)."""
    if owner not in OWNERS:
        raise SkillError(f"unknown owner {owner!r} (use one of: {', '.join(OWNERS)})")
    data = content.replace("\r\n", "\n")
    if len(data.encode()) > MAX_FILE_BYTES:
        raise SkillError("SKILL.md too large")
    try:
        front, _ = parse_frontmatter(data)
    except yaml.YAMLError as exc:
        raise SkillError(f"frontmatter is not valid YAML: {str(exc)[:120]}") from exc
    name = str((front or {}).get("name", "")).strip()
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillError(f"name must be lowercase-with-dashes (got {name!r})")
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / name / "SKILL.md"
        probe.parent.mkdir()
        probe.write_text(data, encoding="utf-8")
        try:  # same rules as the gateway (description, metadata, hub-share, gbrain fields)
            load_skill(probe, owner)
        except SkillError as exc:
            raise SkillError(str(exc).replace(f"{probe}: ", "")) from None
    existing, _ = scan(root)
    target = root / "skills" / owner / name
    if edit is None:
        if name in existing or target.exists():
            raise SkillError(f"a skill named {name!r} already exists")
    else:
        if edit != name:
            raise SkillError("renaming is not supported: keep the same name (create a new skill instead)")
        cur = existing.get(name)
        if cur is None or cur.owner != owner:
            raise SkillError(f"skill {owner}/{name} not found")
    target.mkdir(parents=True, exist_ok=True)
    md = target / "SKILL.md"
    if md.is_symlink():
        raise SkillError("SKILL.md is a symlink; refusing to write")
    fd, tmp = tempfile.mkstemp(dir=target, prefix=".SKILL.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, md)
    return {"name": name, "owner": owner, "path": f"skills/{owner}/{name}/SKILL.md", "created": edit is None}
