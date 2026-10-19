"""Spaces screen (local mode): name, colour, one-line description and read-only bridges, edited from the app.

Spaces are the contexts of contexts.yaml; the UI word is "space". The key (e.g. `work`) never changes, so data
folders and existing connectors keep working; `label` is only what the user sees. Edits rewrite contexts.yaml
(backup in data/contexts.yaml.bak first) and then reload the running servers in place (local.reloader), so the
AI apps get the new descriptions and bridges without a restart. stdio processes launched by AI apps notice the
change on their next call (LocalRouter checks the file's mtime).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

PALETTE = ["#7c5cff", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#f472b6", "#a3e635", "#60a5fa"]
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SHARED_HELP = ("Shared holds what every space should know: your name, language, how you like answers. Every space "
               "and every app can read it. Notes only get there when you choose “Promote to shared”.")


def _file(home: Path) -> Path:
    return Path(home) / "contexts.yaml"


def _load(home: Path) -> dict[str, Any]:
    return yaml.safe_load(_file(home).read_text(encoding="utf-8")) or {}


def _write(home: Path, data: dict[str, Any]) -> None:
    """Validate with the real parser, back up, write atomically."""
    from .contexts import parse_bridges, parse_contexts, parse_instance
    _owner, ctxs = parse_contexts(data)
    parse_instance(data)
    parse_bridges(data, ctxs)
    f = _file(home)
    bak = Path(home) / "data" / "contexts.yaml.bak"
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, bak)
    tmp = f.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, f)


def read(home: Path) -> dict[str, Any]:
    data = _load(home)
    raw = data.get("contexts") or {}
    spaces = []
    for i, (name, spec) in enumerate(raw.items()):
        spec = spec or {}
        spaces.append({"name": name, "label": str(spec.get("label") or name.capitalize()),
                       "color": spec.get("color") if _COLOR.match(str(spec.get("color") or "")) else PALETTE[i % len(PALETTE)],
                       "description": str(spec.get("description") or "")})
    bridges = [{"from": str(b.get("from")), "to": str(b.get("to"))} for b in (data.get("bridges") or [])
               if isinstance(b, dict)]
    return {"spaces": spaces, "bridges": bridges, "shared": SHARED_HELP, "palette": PALETTE}


def update_space(home: Path, name: str, label: str | None = None, color: str | None = None,
                 description: str | None = None) -> dict[str, Any]:
    data = _load(home)
    raw = data.get("contexts") or {}
    if name not in raw:
        raise ValueError(f"unknown space {name!r}")
    spec = dict(raw[name] or {})
    if label is not None:
        label = " ".join(str(label).split())
        if not 1 <= len(label) <= 40:
            raise ValueError("name: 1-40 characters")
        spec["label"] = label
    if color is not None:
        if not _COLOR.match(str(color)):
            raise ValueError("colour: #rrggbb")
        spec["color"] = str(color).lower()
    if description is not None:
        description = " ".join(str(description).split())
        if not 3 <= len(description) <= 200:
            raise ValueError("say in one line what goes in this space (3-200 characters)")
        spec["description"] = description
    raw[name] = spec
    data["contexts"] = raw
    _write(home, data)
    return read(home)


def set_bridge(home: Path, src: str, dst: str, on: bool) -> dict[str, Any]:
    """`dst` may READ `src` (never write, edit or delete). Off removes it."""
    data = _load(home)
    raw = data.get("contexts") or {}
    if src not in raw or dst not in raw or src == dst:
        raise ValueError("pick two different spaces")
    bridges = [b for b in (data.get("bridges") or []) if isinstance(b, dict)
               and not (b.get("from") == src and b.get("to") == dst)]
    if on:
        bridges.append({"from": src, "to": dst})
    if bridges:
        data["bridges"] = bridges
    else:
        data.pop("bridges", None)
    _write(home, data)
    return read(home)
