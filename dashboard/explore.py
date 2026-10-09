"""Pestaña «Explorar» del dashboard: contenido REAL de la memoria, skills y nombres de secretos por contexto.

Aislamiento (lo mismo que ve cada gateway, ni más ni menos):
  * work / personal / side → la API key de Cognee de ESE contexto (COGNEE_KEY_<CTX>, la del gateway):
    Cognee solo le deja leer sus datasets + shared, así que otro contexto no puede filtrarse aunque haya un bug acá.
  * todos → hub-admin (COGNEE_PW_ADMIN), la vista global que ya usa scripts/visualize.py.
Secretos: solo NOMBRES, descripción y hosts permitidos de config/secret-policy.yaml; nunca valores.
Todas las credenciales quedan del lado del servidor; server.py revisa cada respuesta contra los valores de .env.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.contexts import ALL_DATASET_NAMES, CONTEXTS, SHARED
from hub_gateway.skills import MAX_FILE_BYTES, scan

COGNEE = "http://127.0.0.1:8010"


def policy_path() -> Path:
    """config/secret-policy.yaml de la instancia; si todavía no existe, el ejemplo versionado."""
    real = ROOT / "config" / "secret-policy.yaml"
    return real if real.exists() else ROOT / "config" / "secret-policy.example.yaml"

VIEWS = (*CONTEXTS, "todos")
ADMIN_EMAIL = "hub-admin@example.com"
# skills-sync se resuelve con `docker compose` desde la raíz del repo (respeta COMPOSE_PROJECT_NAME/COMPOSE_FILE de .env)
SKILLS_EXEC = ["docker", "compose", "exec", "-T", "skills-sync"]
MAX_TEXT = 6000
MAX_NODES = 2500
# tipos de nodo de Cognee que son "conceptos" (los que conectan memorias entre sí)
ENTITY_TYPES = {"Entity", "EntityType"}


def dataset_context(name: str) -> str:
    if name == SHARED:
        return SHARED
    for c, spec in CONTEXTS.items():
        if name in spec.own_dataset_names:
            return c
    return "?"


def view_datasets(view: str) -> list[str]:
    if view == "todos":
        return list(ALL_DATASET_NAMES)
    return [*CONTEXTS[view].own_dataset_names, SHARED]


def norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def merge_graphs(parts: list[tuple[str, dict]], view: str) -> dict:
    """Une los grafos por dataset. Las entidades con el mismo nombre (o id) se funden en un solo nodo:
    así lo que dos datasets/contextos tienen en común queda conectado y se marca `common`."""
    nodes: dict[str, dict] = {}
    alias: dict[tuple[str, str], str] = {}  # (dataset, id original) -> id final
    for ds, g in parts:
        for n in g.get("nodes", []):
            ntype = str(n.get("type", ""))
            props = n.get("properties") or {}
            if ntype in ENTITY_TYPES:
                key = f"{ntype}:{norm_label(n.get('label', ''))}"
            else:
                key = str(n.get("id"))
            alias[(ds, str(n.get("id")))] = key
            node = nodes.get(key)
            if node is None:
                if len(nodes) >= MAX_NODES:
                    continue
                label = str(n.get("label", ""))
                if re.match(rf"^{re.escape(ntype)}_[0-9a-f-]{{36}}$", label):
                    label = ntype  # Cognee rotula los nodos sin nombre como Tipo_<uuid>
                text = props.get("description") or props.get("text") or props.get("summary") or ""
                node = nodes[key] = {"id": key, "label": label[:80], "type": ntype,
                                     "desc": str(text)[:600], "datasets": []}
            if ds not in node["datasets"]:
                node["datasets"].append(ds)
    edges: dict[tuple[str, str, str], dict] = {}
    for ds, g in parts:
        for e in g.get("edges", []):
            s, t = alias.get((ds, str(e.get("source")))), alias.get((ds, str(e.get("target"))))
            if not s or not t or s == t or s not in nodes or t not in nodes:
                continue
            k = (s, t, str(e.get("label", "")))
            if k not in edges:
                edges[k] = {"source": s, "target": t, "label": k[2][:60]}
    for node in nodes.values():
        ctxs = sorted({dataset_context(d) for d in node["datasets"]})
        node["contexts"] = ctxs
        # "en común": en la vista global, entre contextos distintos; en un contexto, entre 2+ datasets
        # (p. ej. su dataset y shared, o dos proyectos de side)
        spread = len(ctxs) if view == "todos" else len(node["datasets"])
        node["common"] = node["type"] in ENTITY_TYPES and spread > 1
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


class SkillsMirror:
    """Copia de solo lectura del repo de skills (del contenedor skills-sync) en dev/state/, refrescada cada 60 s."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._at = 0.0

    def ensure(self) -> Path:
        with self._lock:
            if time.time() - self._at < 60 and (self.root / "skills").is_dir():
                return self.root
            r = subprocess.run([*SKILLS_EXEC, "tar", "-C", "/skills", "-cf", "-", "skills"],
                               capture_output=True, timeout=30, check=False, cwd=ROOT)
            if r.returncode != 0:
                raise RuntimeError(f"skills-sync exit {r.returncode}")
            tmp = self.root.with_name(self.root.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
                tf.extractall(tmp, filter="data")  # sin rutas absolutas, sin .., sin links fuera
            shutil.rmtree(self.root, ignore_errors=True)
            tmp.rename(self.root)
            self._at = time.time()
            return self.root


class Explorer:
    def __init__(self, env: dict[str, str], err) -> None:
        self.env = env
        self.err = err
        ds_file = ROOT / "config" / "cognee-datasets.json"  # lo genera scripts/bootstrap_cognee.py
        self.state = json.loads(ds_file.read_text()) if ds_file.exists() else {"datasets": {}, "users": {}}
        self.skills = SkillsMirror(ROOT / "dev" / "state" / "skills-mirror")
        self._cache: dict[str, tuple[float, dict]] = {}
        self._admin: tuple[float, httpx.Client] | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- credenciales (solo servidor)
    def _check_cognee(self) -> None:
        hr = httpx.get(f"{COGNEE}/health", timeout=8)
        if hr.status_code != 200 or hr.headers.get("server") != "uvicorn" or "version" not in hr.json():
            raise RuntimeError("not Cognee")  # no mandar credenciales a otra cosa en :8010

    def _client(self, view: str) -> httpx.Client:
        if view in CONTEXTS:
            key = self.env.get(f"COGNEE_KEY_{view.upper()}")
            if not key:
                raise RuntimeError(f"missing key for context {view}")
            return httpx.Client(base_url=COGNEE, timeout=60, headers={"X-Api-Key": key})
        with self._lock:
            if self._admin and time.time() - self._admin[0] < 600:
                return self._admin[1]
            pw = self.env.get("COGNEE_PW_ADMIN")
            if not pw:
                raise RuntimeError("falta COGNEE_PW_ADMIN")
            c = httpx.Client(base_url=COGNEE, timeout=60)
            r = c.post("/api/v1/auth/login", data={"username": ADMIN_EMAIL, "password": pw})
            r.raise_for_status()
            try:
                token = r.json().get("access_token")
            except ValueError:
                token = None
            if token:
                c.headers["Authorization"] = f"Bearer {token}"
            self._admin = (time.time(), c)
            return c

    # ---------------------------------------------------------------- vista
    def explore(self, view: str, fresh: bool = False) -> dict:
        if view not in VIEWS:
            raise ValueError("invalid view")
        hit = self._cache.get(view)
        if hit and not fresh and time.time() - hit[0] < 30:
            return hit[1]
        out: dict[str, Any] = {"view": view, "identity": "hub-admin" if view == "todos" else f"ctx-{view}",
                               "datasets": [], "errors": []}
        try:
            self._check_cognee()
            out.update(self._memory(view))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"memoria: {self.err(e)}")
            out.setdefault("graph", {"nodes": [], "edges": []})
            out.setdefault("memories", [])
        try:
            out["skills"] = self._skills(view)
        except Exception as e:  # noqa: BLE001
            out["skills"] = []
            out["errors"].append(f"skills: {self.err(e)}")
        out["secrets"] = self._secrets(view)
        out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._cache[view] = (time.time(), out)
        return out

    def _memory(self, view: str) -> dict:
        c = self._client(view)
        ids = self.state["datasets"]
        parts: list[tuple[str, dict]] = []
        memories: list[dict] = []
        datasets: list[dict] = []
        try:
            for name in view_datasets(view):
                did = ids.get(name)
                if not did:
                    continue
                info: dict[str, Any] = {"name": name, "context": dataset_context(name)}
                g = c.get(f"/api/v1/datasets/{did}/graph")
                if g.status_code == 200:
                    parts.append((name, g.json()))
                    info["nodes"], info["edges"] = len(g.json().get("nodes", [])), len(g.json().get("edges", []))
                else:
                    info["error"] = f"grafo HTTP {g.status_code}"
                done: dict[str, bool] = {}
                ps = c.get(f"/api/v1/datasets/{did}/processing-status")
                if ps.status_code == 200:
                    done = {str(i.get("id")): bool(i.get("completed")) for i in ps.json().get("items", [])}
                    info["pending"] = ps.json().get("pending")
                dl = c.get(f"/api/v1/datasets/{did}/data")
                if dl.status_code == 200:
                    for d in dl.json():
                        memories.append(self._memory_item(c, did, name, d, done))
                    info["items"] = len(dl.json())
                else:
                    info.setdefault("error", f"datos HTTP {dl.status_code}")
                datasets.append(info)
        finally:
            if view != "todos":
                c.close()
        memories.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        graph = merge_graphs(parts, view)
        return {"datasets": datasets, "memories": memories, "graph": graph}

    @staticmethod
    def _memory_item(c: httpx.Client, did: str, name: str, d: dict, done: dict[str, bool]) -> dict:
        meta = d.get("externalMetadata") or d.get("external_metadata") or {}
        item = {"id": str(d.get("id")), "dataset": name, "context": dataset_context(name),
                "created_at": d.get("createdAt") or d.get("created_at"),
                "source_app": meta.get("source_app") or d.get("label"),
                "tags": meta.get("tags") or [], "in_graph": done.get(str(d.get("id")))}
        mime = str(d.get("mimeType") or d.get("mime_type") or "")
        if mime.startswith("text/"):
            r = c.get(f"/api/v1/datasets/{did}/data/{d.get('id')}/raw")
            if r.status_code == 200:
                text = r.content[: MAX_TEXT * 4].decode("utf-8", errors="replace")
                item["text"], item["truncated"] = text[:MAX_TEXT], len(text) > MAX_TEXT
            else:
                item["text_error"] = f"HTTP {r.status_code}"
        return item

    # ---------------------------------------------------------------- skills
    def _visible_skills(self, view: str) -> list:
        skills, _errors = scan(self.skills.ensure())
        return [s for s in skills.values() if view == "todos" or s.visible_in(view)]

    def _skills(self, view: str) -> list[dict]:
        return sorted(({"name": s.name, "owner": s.owner, "description": s.description[:400],
                        "share": sorted(s.share)} for s in self._visible_skills(view)),
                      key=lambda s: (s["owner"] != SHARED, s["owner"], s["name"]))

    def skill(self, view: str, name: str) -> dict:
        if view not in VIEWS:
            raise ValueError("invalid view")
        s = next((s for s in self._visible_skills(view) if s.name == name), None)
        if s is None:
            raise LookupError("skill not visible in this context")
        md = s.path / "SKILL.md"
        if md.is_symlink() or md.stat().st_size > MAX_FILE_BYTES:
            raise LookupError("SKILL.md not readable")
        files = sorted(str(p.relative_to(s.path)) for p in s.path.rglob("*") if p.is_file() and not p.is_symlink())
        return {"name": s.name, "owner": s.owner, "content": md.read_text(encoding="utf-8", errors="replace"),
                "files": files[:50]}

    # ---------------------------------------------------------------- secretos (solo nombres)
    @staticmethod
    def _secrets(view: str) -> list[dict]:
        data = yaml.safe_load(policy_path().read_text()) or {}
        out = []
        for ctx in CONTEXTS if view == "todos" else (view,):
            for name, spec in (data.get(ctx) or {}).items():
                spec = spec or {}
                out.append({"name": name, "context": ctx, "description": str(spec.get("description", ""))[:200],
                            "hosts": list(spec.get("allowed_hosts", [])), "methods": spec.get("methods") or [],
                            "header": (spec.get("inject") or {}).get("header")})
        return out
