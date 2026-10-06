"""Índice de skills (formato Agent Skills) filtrado por contexto.

Layout esperado del repo de skills (clonado por skills-sync en HUB_SKILLS_ROOT):

    skills/<owner>/<name>/SKILL.md     owner ∈ {shared, work, personal, side}

Visibilidad:
  * skills/shared/**  -> visible en todos los contextos
  * skills/<ctx>/**   -> visible en <ctx> y en los contextos listados en metadata.hub-share
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .contexts import CONTEXTS, SHARED

OWNERS = (SHARED, *CONTEXTS.keys())
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_FILE_BYTES = 256 * 1024


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    description: str
    owner: str
    path: Path  # directorio de la skill
    share: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, str] = field(default_factory=dict)

    def visible_in(self, context: str) -> bool:
        return self.owner == SHARED or self.owner == context or context in self.share


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise SkillError("SKILL.md sin frontmatter YAML")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise SkillError("frontmatter sin cierre '---'")
    header = parts[0][3:]
    body = parts[1].lstrip("-").lstrip("\n")
    data = yaml.safe_load(header) or {}
    if not isinstance(data, dict):
        raise SkillError("frontmatter no es un mapa")
    return data, body


def load_skill(skill_md: Path, owner: str) -> Skill:
    data, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = str(data.get("name", "")).strip()
    desc = str(data.get("description", "")).strip()
    folder = skill_md.parent.name
    if not name or not desc:
        raise SkillError(f"{skill_md}: faltan name o description")
    if name != folder:
        raise SkillError(f"{skill_md}: name {name!r} no coincide con la carpeta {folder!r}")
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillError(f"{skill_md}: name inválido {name!r}")
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        raise SkillError(f"{skill_md}: metadata debe ser un mapa")
    meta = {str(k): str(v) for k, v in meta.items()}
    declared_owner = meta.get("hub-owner")
    if declared_owner and declared_owner != owner:
        raise SkillError(f"{skill_md}: hub-owner={declared_owner!r} pero está en skills/{owner}/")
    share = frozenset(s for s in meta.get("hub-share", "").split() if s)
    unknown = share - set(CONTEXTS)
    if unknown:
        raise SkillError(f"{skill_md}: hub-share con contextos desconocidos: {sorted(unknown)}")
    return Skill(name=name, description=desc, owner=owner, path=skill_md.parent, share=share, metadata=meta)


def scan(root: Path) -> tuple[dict[str, Skill], list[str]]:
    """Devuelve (skills por nombre, errores). Nombres duplicados se descartan ambos."""
    skills: dict[str, Skill] = {}
    errors: list[str] = []
    dupes: set[str] = set()
    base = root / "skills"
    if not base.is_dir():
        return {}, [f"no existe {base}"]
    for owner in OWNERS:
        odir = base / owner
        if not odir.is_dir():
            continue
        for skill_md in sorted(odir.glob("*/SKILL.md")):
            try:
                s = load_skill(skill_md, owner)
            except (SkillError, yaml.YAMLError, OSError) as exc:
                errors.append(str(exc))
                continue
            if s.name in skills:
                dupes.add(s.name)
                errors.append(f"nombre duplicado: {s.name}")
                continue
            skills[s.name] = s
    for d in dupes:
        skills.pop(d, None)
    return skills, errors


class SkillIndex:
    """Índice perezoso: se reconstruye si cambió el repo (revisa como mucho cada `min_interval` s)."""

    def __init__(self, root: str | Path, context: str, min_interval: float = 5.0) -> None:
        self.root = Path(root)
        self.context = context
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._sig: object = None
        self._checked = 0.0
        self._skills: dict[str, Skill] = {}
        self.errors: list[str] = []

    def _signature(self) -> object:
        head = self.root / ".git" / "HEAD"
        entries: list[tuple[str, float]] = []
        try:
            for dirpath, _dirs, files in os.walk(self.root / "skills"):
                for f in files:
                    p = os.path.join(dirpath, f)
                    entries.append((p, os.stat(p).st_mtime))
        except OSError:
            pass
        head_val = head.read_text() if head.exists() else ""
        return (head_val, tuple(sorted(entries)))

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._checked < self.min_interval and self._sig is not None:
            return
        with self._lock:
            self._checked = now
            sig = self._signature()
            if sig != self._sig:
                self._skills, self.errors = scan(self.root)
                self._sig = sig

    def visible(self) -> list[Skill]:
        self._refresh()
        return sorted((s for s in self._skills.values() if s.visible_in(self.context)), key=lambda s: s.name)

    def get(self, name: str) -> Skill:
        self._refresh()
        s = self._skills.get(name)
        # Una skill de otro contexto es indistinguible de una inexistente.
        if s is None or not s.visible_in(self.context):
            raise SkillError(f"skill no encontrada: {name}")
        return s

    def read(self, name: str, file: str | None = None) -> str:
        s = self.get(name)
        rel = file or "SKILL.md"
        target = (s.path / rel).resolve()
        base = s.path.resolve()
        if base != target and base not in target.parents:
            raise SkillError("ruta fuera de la skill")
        if not target.is_file():
            raise SkillError(f"archivo no encontrado en la skill: {rel}")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise SkillError("archivo demasiado grande")
        return target.read_text(encoding="utf-8", errors="replace")

    def files(self, name: str) -> list[str]:
        s = self.get(name)
        out = []
        for p in sorted(s.path.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                out.append(str(p.relative_to(s.path)))
        return out
