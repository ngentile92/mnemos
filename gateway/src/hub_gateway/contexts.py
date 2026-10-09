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
    # Frase corta para las instrucciones de los OTROS contextos ("personal stuff goes to hub-personal").
    topic: str = ""

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
            topic=str(spec.get("topic") or "").strip(),
        )
    return owner, out


LANGUAGES = ("en", "es")


def parse_instance(data: dict[str, Any]) -> tuple[str, str]:
    """(server_name_prefix, language) de la instancia. El prefijo es el nombre MCP visible ("<prefix>-<ctx>")."""
    prefix = str(data.get("server_name_prefix") or "mnemos").strip()
    if not _NAME_RE.match(prefix):
        raise ValueError(f"contexts.yaml: server_name_prefix inválido: {prefix!r}")
    lang = str(data.get("language") or "en").strip().lower()
    if lang not in LANGUAGES:
        raise ValueError(f"contexts.yaml: language debe ser uno de {LANGUAGES}, no {lang!r}")
    return prefix, lang


@dataclass(frozen=True)
class Bridge:
    """Opt-in: el contexto `reader` puede LEER (nunca escribir, corregir ni borrar) datasets de `source`."""
    source: str
    reader: str
    datasets: tuple[str, ...]


def parse_bridges(data: dict[str, Any], contexts: dict[str, ContextSpec]) -> list[Bridge]:
    """Sección opcional `bridges:` de contexts.yaml:

        bridges:
          - from: side            # contexto dueño de los datos
            to: work              # contexto que los puede leer
            datasets: [side_mnemos]   # opcional; por defecto todos los de `from`
    """
    raw = data.get("bridges") or []
    if not isinstance(raw, list):
        raise ValueError("contexts.yaml: `bridges` debe ser una lista")
    out: list[Bridge] = []
    for b in raw:
        if not isinstance(b, dict):
            raise ValueError("contexts.yaml: cada bridge necesita `from` y `to`")
        src, dst = str(b.get("from") or ""), str(b.get("to") or "")
        if src not in contexts or dst not in contexts:
            raise ValueError(f"contexts.yaml: bridge con contexto desconocido: {src!r} → {dst!r}")
        if src == dst:
            raise ValueError(f"contexts.yaml: bridge de {src!r} a sí mismo")
        own = contexts[src].own_dataset_names
        names = b.get("datasets")
        if names is None:
            names = own
        if not isinstance(names, list) or not names:
            raise ValueError(f"contexts.yaml: bridge {src}→{dst}: `datasets` debe ser una lista no vacía")
        for d in names:
            if d not in own:
                raise ValueError(f"contexts.yaml: bridge {src}→{dst}: {d!r} no es un dataset de {src!r}")
        out.append(Bridge(src, dst, tuple(dict.fromkeys(str(d) for d in names))))
    return out


def bridged_for(reader: str, bridges: list[Bridge]) -> list[str]:
    """Datasets de OTROS contextos que `reader` puede leer por bridges (en orden, sin repetir)."""
    return list(dict.fromkeys(d for b in bridges if b.reader == reader for d in b.datasets))


_CONFIG = load_config()
OWNER, CONTEXTS = parse_contexts(_CONFIG)
SERVER_NAME_PREFIX, LANGUAGE = parse_instance(_CONFIG)
BRIDGES = parse_bridges(_CONFIG, CONTEXTS)

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
