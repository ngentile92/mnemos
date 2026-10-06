"""Contextos y mapeo a datasets de Cognee, leídos de config/contexts.yaml.

El contexto lo fija la instancia (variable HUB_CONTEXT). El modelo nunca puede cambiarlo.

Orden de búsqueda del archivo de contextos:
  1. HUB_CONTEXTS_FILE (si está definida),
  2. /config/contexts.yaml (montado en los contenedores),
  3. <repo>/config/contexts.yaml y <repo>/config/contexts.example.yaml,
  4. DEFAULT_CONFIG (work / personal / side), igual a config/contexts.example.yaml.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHARED = "shared"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")

DEFAULT_CONFIG: dict[str, Any] = {
    "owner": "the owner",
    "contexts": {
        "work": {"description": "the owner's job: company repos, clients, tickets and work chat"},
        "personal": {"description": "personal life and personal projects: home, finances, purchases, interests"},
        "side": {
            "description": "side projects (Shop, Blog and this hub, Mnemos)",
            "projects": {"shop": "side_shop", "blog": "side_blog", "mnemos": "side_mnemos"},
        },
    },
}


@dataclass(frozen=True)
class ContextSpec:
    name: str
    # proyecto lógico -> nombre de dataset Cognee. "" = proyecto por defecto.
    datasets: dict[str, str] = field(default_factory=dict)
    # Si True, memory_save exige `project` (contextos con subproyectos).
    project_required: bool = False
    description: str = ""

    @property
    def projects(self) -> list[str]:
        return [p for p in self.datasets if p]

    @property
    def own_dataset_names(self) -> list[str]:
        return list(dict.fromkeys(self.datasets.values()))


def _candidate_files() -> list[Path]:
    env = os.environ.get("HUB_CONTEXTS_FILE")
    if env:
        return [Path(env)]
    repo_config = Path(__file__).resolve().parents[3] / "config"
    return [Path("/config/contexts.yaml"), repo_config / "contexts.yaml", repo_config / "contexts.example.yaml"]


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    files = [Path(path)] if path else _candidate_files()
    for f in files:
        if f.is_file():
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if path or os.environ.get("HUB_CONTEXTS_FILE"):
        raise ValueError(f"no existe el archivo de contextos: {files[0]}")
    return DEFAULT_CONFIG


def parse_contexts(data: dict[str, Any]) -> tuple[str, dict[str, ContextSpec]]:
    owner = str(data.get("owner") or "the owner").strip()
    raw = data.get("contexts") or {}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("contexts.yaml: falta la sección `contexts`")
    out: dict[str, ContextSpec] = {}
    for name, spec in raw.items():
        spec = spec or {}
        if not _NAME_RE.match(str(name)) or name == SHARED:
            raise ValueError(f"contexts.yaml: nombre de contexto inválido: {name!r}")
        projects = spec.get("projects") or {}
        if projects:
            datasets = {str(p): str(d or f"{name}_{p}".replace("-", "")) for p, d in projects.items()}
        else:
            datasets = {"": str(spec.get("dataset") or name)}
        for d in datasets.values():
            if not re.match(r"^[a-z][a-z0-9_]*$", d) or d == SHARED:
                raise ValueError(f"contexts.yaml: dataset inválido en {name!r}: {d!r}")
        out[name] = ContextSpec(
            name=name,
            datasets=datasets,
            project_required=bool(spec.get("project_required", bool(projects))),
            description=str(spec.get("description") or name).strip(),
        )
    return owner, out


OWNER, CONTEXTS = parse_contexts(load_config())

ALL_DATASET_NAMES = [SHARED] + [d for c in CONTEXTS.values() for d in c.own_dataset_names]
if len(set(ALL_DATASET_NAMES)) != len(ALL_DATASET_NAMES):
    raise ValueError("contexts.yaml: hay datasets repetidos entre contextos")

# Qué usuario de Cognee es dueño de cada dataset (lo usa el bootstrap).
DATASET_OWNER = {SHARED: "hub-admin"} | {
    d: f"ctx-{c.name}" for c in CONTEXTS.values() for d in c.own_dataset_names
}


def get_context(name: str) -> ContextSpec:
    try:
        return CONTEXTS[name]
    except KeyError as exc:  # pragma: no cover - validado al arrancar
        raise ValueError(
            f"HUB_CONTEXT inválido: {name!r}. Opciones: {', '.join(CONTEXTS)}"
        ) from exc
