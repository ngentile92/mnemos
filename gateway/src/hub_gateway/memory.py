"""Cliente de la API REST de Cognee con el alcance fijado por contexto.

Reglas:
  * siempre se usan UUIDs de dataset (los nombres se resuelven por usuario en Cognee);
  * el contexto (y por lo tanto los datasets) lo fija la instancia, nunca el modelo;
  * lectura sin LLM: `only_context=true` y `scope="graph"` (sin sesiones ni tools externas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .contexts import SHARED, ContextSpec

MAX_TEXT_CHARS = 20_000


class MemoryError_(ValueError):
    """Error de validación de memoria (mensaje apto para el modelo)."""


@dataclass(frozen=True)
class DatasetMap:
    ids: dict[str, str]  # nombre -> UUID

    @classmethod
    def load(cls, path: str) -> "DatasetMap":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        ids = data.get("datasets", data)
        return cls({str(k): str(v) for k, v in ids.items()})

    def id_of(self, name: str) -> str:
        try:
            return self.ids[name]
        except KeyError as exc:
            raise MemoryError_(
                f"dataset {name!r} no está en cognee-datasets.json; corré scripts/bootstrap_cognee.py"
            ) from exc


class MemoryScope:
    """Traduce los argumentos de los tools a datasets permitidos para el contexto."""

    def __init__(self, ctx: ContextSpec, datasets: DatasetMap) -> None:
        self.ctx = ctx
        self.datasets = datasets

    def read_ids(self, include_shared: bool = True, project: str | None = None) -> list[str]:
        if project:
            names = [self._dataset_for_project(project)]
        else:
            names = list(self.ctx.own_dataset_names)
        if include_shared:
            names.append(SHARED)
        return [self.datasets.id_of(n) for n in names]

    def write_id(self, target: str = "context", project: str | None = None) -> tuple[str, str]:
        if target == "shared":
            if project:
                raise MemoryError_("project no aplica cuando target='shared'")
            return SHARED, self.datasets.id_of(SHARED)
        if target != "context":
            raise MemoryError_("target debe ser 'context' o 'shared'")
        if project is None:
            if self.ctx.project_required:
                raise MemoryError_(
                    f"en el contexto {self.ctx.name} tenés que indicar project: {', '.join(self.ctx.projects)}"
                )
            name = self.ctx.datasets[""]
        else:
            name = self._dataset_for_project(project)
        return name, self.datasets.id_of(name)

    def _dataset_for_project(self, project: str) -> str:
        if project not in self.ctx.projects:
            opts = ", ".join(self.ctx.projects) or "(este contexto no tiene subproyectos)"
            raise MemoryError_(f"project inválido {project!r}. Opciones: {opts}")
        return self.ctx.datasets[project]

    def allowed_ids(self) -> set[str]:
        return {self.datasets.id_of(n) for n in [*self.ctx.own_dataset_names, SHARED]}

    def listable(self, include_shared: bool = False, project: str | None = None) -> list[tuple[str, str]]:
        """(nombre, UUID) de los datasets que el contexto puede listar."""
        names = [self._dataset_for_project(project)] if project else list(self.ctx.own_dataset_names)
        if include_shared:
            names.append(SHARED)
        return [(n, self.datasets.id_of(n)) for n in names]

    def editable(self) -> list[tuple[str, str]]:
        """Datasets donde el contexto puede borrar/corregir: SOLO los propios, nunca `shared`.
        `shared` se corrige solo como hub-admin (scripts/memory_admin.py)."""
        return [(n, self.datasets.id_of(n)) for n in self.ctx.own_dataset_names]


class CogneeClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 search_type: str = "GRAPH_COMPLETION") -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Api-Key": api_key} if api_key else {}
        self.timeout = timeout
        self.transport = transport
        self.search_type = search_type

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self.headers,
                                 timeout=self.timeout, transport=self.transport, trust_env=False)

    async def recall(self, query: str, dataset_ids: list[str], top_k: int = 10,
                     search_type: str | None = None) -> Any:
        if not dataset_ids:
            raise MemoryError_("sin datasets: el gateway nunca consulta sin alcance explícito")
        payload = {
            "query": query,
            "dataset_ids": dataset_ids,
            "search_type": search_type or self.search_type,
            "only_context": True,
            "scope": "graph",
            "top_k": top_k,
        }
        async with self._client() as c:
            r = await c.post("/api/v1/recall", json=payload, headers={"Accept": "application/json"})
        if r.status_code == 404 and "NoDataError" in r.text:
            return []  # dataset(s) todavía vacíos: no es un error para el modelo
        if r.status_code in (403, 404):
            raise MemoryError_(f"Cognee negó el acceso o no encontró datasets (HTTP {r.status_code})")
        r.raise_for_status()
        return r.json()

    async def remember(self, text: str, dataset_id: str, node_set: list[str] | None = None,
                       run_in_background: bool = True, metadata: dict[str, Any] | None = None) -> Any:
        if not text.strip():
            raise MemoryError_("texto vacío")
        if len(text) > MAX_TEXT_CHARS:
            raise MemoryError_(f"texto demasiado largo (máx {MAX_TEXT_CHARS} caracteres)")
        form: list[tuple[str, str]] = [
            ("raw_data", text),
            ("datasetId", dataset_id),
            ("run_in_background", "true" if run_in_background else "false"),
        ]
        if metadata:
            # una entrada por ítem (mandamos uno solo); 'node_set' es una clave reservada de Cognee
            clean = {k: v for k, v in metadata.items() if k != "node_set" and v not in (None, "", [])}
            form.append(("external_metadata", json.dumps([clean], ensure_ascii=False)))
        for n in node_set or []:
            form.append(("node_set", n))
        async with self._client() as c:
            # multipart/form-data, como espera el endpoint /api/v1/remember
            r = await c.post("/api/v1/remember", files=[(k, (None, v)) for k, v in form])
        if r.status_code in (403, 404):
            raise MemoryError_(f"Cognee negó la escritura (HTTP {r.status_code})")
        r.raise_for_status()
        return r.json()


    async def list_data(self, dataset_id: str) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(f"/api/v1/datasets/{dataset_id}/data")
        if r.status_code in (401, 403, 404):
            raise MemoryError_(f"Cognee negó el listado (HTTP {r.status_code})")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def raw_text(self, dataset_id: str, data_id: str, max_bytes: int = 4 * MAX_TEXT_CHARS) -> str | None:
        async with self._client() as c:
            r = await c.get(f"/api/v1/datasets/{dataset_id}/data/{data_id}/raw")
        if r.status_code != 200:
            return None
        return r.content[:max_bytes].decode("utf-8", errors="replace")

    async def delete_data(self, dataset_id: str, data_id: str) -> None:
        async with self._client() as c:
            r = await c.delete(f"/api/v1/datasets/{dataset_id}/data/{data_id}")
        if r.status_code in (401, 403, 404):
            raise MemoryError_(f"Cognee negó el borrado (HTTP {r.status_code})")
        r.raise_for_status()


def item_summary(d: dict[str, Any], dataset: str) -> dict[str, Any]:
    meta = d.get("externalMetadata") or d.get("external_metadata") or {}
    if isinstance(meta, list):  # Cognee puede guardar la lista que mandamos
        meta = meta[0] if meta and isinstance(meta[0], dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": str(d.get("id")),
        "dataset": dataset,
        "created_at": d.get("createdAt") or d.get("created_at"),
        "source_app": meta.get("source_app") or d.get("label"),
        "tags": meta.get("tags") or [],
    }


def simplify_results(results: Any, allowed_ids: set[str], max_chars: int = 12_000) -> list[dict[str, Any]]:
    """Reduce la respuesta de Cognee a lo útil y descarta cualquier resultado de datasets ajenos."""
    out: list[dict[str, Any]] = []
    total = 0
    if isinstance(results, dict):
        results = results.get("results", [results])
    for item in results or []:
        if not isinstance(item, dict):
            item = {"text": str(item)}
        ds = item.get("dataset_id") or item.get("datasetId")
        if ds is not None and str(ds) not in allowed_ids:
            continue  # defensa en profundidad: Cognee ya filtra, el gateway vuelve a filtrar
        text = item.get("text")
        if text is None:
            text = item.get("search_result", item.get("result", item.get("content")))
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, default=str)
        remaining = max_chars - total
        if remaining <= 0:
            break
        text = text[:remaining]
        total += len(text)
        out.append({
            "dataset": item.get("dataset_name") or item.get("datasetName"),
            "text": text,
        })
    return out
