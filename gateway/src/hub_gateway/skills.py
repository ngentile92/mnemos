"""Índice de skills (formato Agent Skills) filtrado por contexto.

Layout esperado del repo de skills (clonado por skills-sync en HUB_SKILLS_ROOT):

    skills/<owner>/<name>/SKILL.md     owner ∈ {shared, work, personal, side}

Formato: Agent Skills (`name`, `description`, `metadata`). También acepta SKILL.md en formato gbrain
(`triggers`, `version`, `tools`, `mutating`, más claves propias que se ignoran) y los archivos de soporte
de un skillpack de gbrain (`_*.md`, `RESOLVER.md`, `conventions/`): ver docs/skills-gbrain.md.

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
    triggers: tuple[str, ...] = ()
    version: str | None = None
    tools: tuple[str, ...] = ()
    mutating: bool | None = None

    def summary(self) -> dict[str, object]:
        """What skills_list shows: always name/description/owner; gbrain fields only when declared."""
        out: dict[str, object] = {"name": self.name, "description": self.description, "owner": self.owner}
        if self.triggers:
            out["triggers"] = list(self.triggers)
        if self.version:
            out["version"] = self.version
        if self.tools:
            out["tools"] = list(self.tools)
        if self.mutating is not None:
            out["mutating"] = self.mutating
        return out

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


# gbrain tool names -> closest Mnemos tool (for skills written for gbrain). None = no equivalent.
GBRAIN_TOOL_MAP: dict[str, str] = {
    "recall": "memory_search", "search": "memory_search", "query": "memory_search",
    "get_backlinks": "memory_search", "traverse_graph": "memory_search", "get_timeline": "memory_search",
    "get_page": "memory_list", "list_pages": "memory_list",
    "put_page": "memory_save", "add_timeline_entry": "memory_save", "put_raw_data": "memory_save",
}
MAX_TRIGGERS = 50


def _str_list(value: object, key: str, where: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
        raise SkillError(f"{where}: {key} debe ser una lista de textos")
    return tuple(str(v).strip() for v in value if str(v).strip())


def tool_equivalents(tools: tuple[str, ...]) -> dict[str, str | None]:
    """For a skill's declared tools: Mnemos tool to use instead (None if there is none)."""
    mnemos = {"memory_search", "memory_save", "memory_list", "memory_update", "memory_delete", "memory_history",
              "memory_undo", "skills_list", "skills_get", "secrets_list", "secret_http_request", "hub_whoami"}
    return {t: (t if t in mnemos else GBRAIN_TOOL_MAP.get(t.removeprefix("mcp:"))) for t in tools}


def load_skill(skill_md: Path, owner: str) -> Skill:
    data, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = str(data.get("name", "")).strip()
    desc = " ".join(str(data.get("description", "")).split())  # gbrain uses multi-line `description: |`
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
    triggers = _str_list(data.get("triggers"), "triggers", skill_md)
    if len(triggers) > MAX_TRIGGERS:
        raise SkillError(f"{skill_md}: demasiados triggers (máx {MAX_TRIGGERS})")
    tools = _str_list(data.get("tools"), "tools", skill_md)
    mutating = data.get("mutating")
    if mutating is not None and not isinstance(mutating, bool):
        raise SkillError(f"{skill_md}: mutating debe ser true o false")
    version = str(data["version"]).strip() if data.get("version") is not None else None
    return Skill(name=name, description=desc, owner=owner, path=skill_md.parent, share=share, metadata=meta,
                 triggers=tuple(t[:200] for t in triggers), version=version or None, tools=tools,
                 mutating=mutating)


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
