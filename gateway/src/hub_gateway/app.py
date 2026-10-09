"""Servidor MCP del hub para UN contexto (HUB_CONTEXT)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ResourceError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .audit import Audit
from .concurrency import DuplicateRequestIdGuard
from .config import Settings
from .contexts import SERVER_NAME_PREFIX, get_context
from .instructions import server_instructions
from .ledger import Ledger, safe
from .memory import CogneeClient, DatasetMap, MemoryError_, MemoryScope, item_summary, simplify_results
from .secrets import InfisicalFetcher, SecretBroker, SecretPolicyError, load_policy
from .skills import SkillError, SkillIndex

log = logging.getLogger("hub.gateway")

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_LIST_SCAN = 500  # tope de notas leídas por memory_list (cada una es un GET a Cognee)
SHARED_EDIT_HINT = ("la memoria compartida (shared) no se borra ni se corrige desde un conector: "
                    "lo hace el dueño como hub-admin con scripts/memory_admin.py")


def _current_login(settings: Settings) -> str | None:
    if settings.dev_no_auth:
        return "dev-no-auth"
    from fastmcp.server.dependencies import get_access_token

    from .auth import login_from_token

    return login_from_token(get_access_token())


_APP_RE = re.compile(r"[^A-Za-z0-9 ._()/-]")


def _client_app() -> str | None:
    """Nombre de la app cliente según el `initialize` de MCP (p. ej. "claude-ai", "ChatGPT", "cursor-vscode").
    Es informativo (lo declara el cliente): sirve para mostrar el origen de cada memoria, no para autorizar."""
    try:
        from fastmcp.server.dependencies import get_context

        params = get_context().session.client_params
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
        name = str(getattr(info, "name", "") or "").strip()
        version = str(getattr(info, "version", "") or "").strip()
    except Exception:  # noqa: BLE001 — sin sesión MCP (tests, llamadas internas)
        return None
    if not name:
        return None
    label = f"{name} {version}".strip() if version else name
    return _APP_RE.sub("", label)[:60] or None


_LINK_RE = re.compile(r"\[\[([^\[\]\n|]{1,60})(?:\|[^\[\]\n]{0,60})?\]\]")
MAX_LINKS = 5


def wikilinks(text: str) -> list[str]:
    """[[Name]] / [[Name|alias]] references in a note, deduplicated (case-insensitive), in order.
    They become Cognee node sets, so every note that links the same name shares one node: cheap,
    deterministic linking with no LLM involved."""
    seen: dict[str, str] = {}
    for m in _LINK_RE.finditer(text):
        name = " ".join(m.group(1).split())
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())[:MAX_LINKS]


def _node_sets(tags: list[str], text: str) -> list[str] | None:
    out = list(tags)
    for link in wikilinks(text):
        if all(link.lower() != t.lower() for t in out):
            out.append(link)
    return out or None


def _returned_id(res: Any) -> str | None:
    """data id from a /remember response, when Cognee includes it."""
    if isinstance(res, dict):
        items = res.get("items") or []
        if len(items) == 1 and isinstance(items[0], dict) and items[0].get("id"):
            return str(items[0]["id"])
    return None


def build_server(
    settings: Settings,
    *,
    cognee: CogneeClient | None = None,
    datasets: DatasetMap | None = None,
    skills: SkillIndex | None = None,
    broker: SecretBroker | None = None,
    audit: Audit | None = None,
    ledger: Ledger | None = None,
) -> FastMCP:
    ctx = get_context(settings.context)
    audit = audit or Audit(settings.context, os.path.join(settings.data_dir, "audit.log"))
    ledger = ledger or Ledger(os.path.join(settings.data_dir, "ledger.sqlite"))
    skills = skills or SkillIndex(settings.skills_root, settings.context)
    cognee = cognee or CogneeClient(
        settings.cognee_url,
        settings.cognee_api_key,
        search_type=os.environ.get("HUB_RECALL_SEARCH_TYPE", "GRAPH_COMPLETION"),
    )
    if broker is None:
        fetcher = None
        if settings.infisical_url and settings.infisical_client_id and settings.infisical_client_secret:
            fetcher = InfisicalFetcher(
                settings.infisical_url,
                settings.infisical_client_id,
                settings.infisical_client_secret,
                settings.infisical_environment,
            )
        broker = SecretBroker(load_policy(settings.secret_policy_file, settings.context), fetcher)

    _datasets = {"map": datasets}

    def scope() -> MemoryScope:
        if _datasets["map"] is None:
            try:
                _datasets["map"] = DatasetMap.load(settings.datasets_file)
            except FileNotFoundError as exc:
                raise ToolError(
                    "falta config/cognee-datasets.json: corré scripts/bootstrap_cognee.py"
                ) from exc
        return MemoryScope(ctx, _datasets["map"])

    auth = None
    middleware = []
    if not settings.dev_no_auth:
        from fastmcp.server.middleware import AuthMiddleware
        from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

        from .auth import build_auth, make_owner_check

        auth = build_auth(settings)
        middleware = [
            AuthMiddleware(auth=make_owner_check(settings.allowed_logins)),
            RateLimitingMiddleware(max_requests_per_second=5, burst_capacity=20),
        ]
    else:
        log.warning("HUB_DEV_NO_AUTH=1: auth DESACTIVADA (solo desarrollo local)")

    mcp = FastMCP(
        f"{SERVER_NAME_PREFIX}-{ctx.name}",
        instructions=server_instructions(ctx),
        auth=auth,
        middleware=middleware,
        mask_error_details=True,
    )

    # ------------------------------------------------------------------ utilidades
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "context": ctx.name})

    @mcp.tool(annotations={**READ_ONLY, "title": "Quién soy"})
    async def hub_whoami() -> dict[str, Any]:
        """Muestra el contexto activo, el usuario autenticado y los proyectos disponibles.
        Usalo para verificar a qué hub estás conectado."""
        login = _current_login(settings)
        audit.log("hub_whoami", login, "ok")
        return {
            "context": ctx.name,
            "login": login,
            "projects": ctx.projects,
            "memory_datasets": [*ctx.own_dataset_names, "shared"],
        }

    # ------------------------------------------------------------------ memoria
    @mcp.tool(annotations={**READ_ONLY, "title": "Buscar en memoria"})
    async def memory_search(
        query: Annotated[str, Field(description="Qué buscar, en lenguaje natural", min_length=2, max_length=2000)],
        include_shared: Annotated[bool, Field(description="Incluir la memoria compartida entre contextos")] = True,
        project: Annotated[str | None, Field(description="Solo contexto side: limitar a un proyecto")] = None,
        top_k: Annotated[int, Field(ge=1, le=30)] = 10,
    ) -> dict[str, Any]:
        """Busca en la memoria de largo plazo del contexto activo (y en la compartida si include_shared).
        Devuelve fragmentos de contexto; no inventa respuestas. Para ver ids de notas (y poder
        corregirlas o borrarlas) usá memory_list."""
        login = _current_login(settings)
        try:
            sc = scope()
            ids = sc.read_ids(include_shared=include_shared, project=project)
            raw = await cognee.recall(query, ids, top_k=top_k)
        except MemoryError_ as exc:
            audit.log("memory_search", login, "rejected", reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:  # errores de red/Cognee: mensaje genérico al modelo
            audit.log("memory_search", login, "error", error=type(exc).__name__)
            raise ToolError("la memoria (Cognee) no respondió; probá de nuevo más tarde") from exc
        results = simplify_results(raw, sc.allowed_ids())
        audit.log("memory_search", login, "ok", datasets=len(ids), results=len(results))
        return {"context": ctx.name, "results": results}

    @mcp.tool(annotations={"title": "Guardar en memoria", "readOnlyHint": False,
                           "destructiveHint": False, "openWorldHint": False})
    async def memory_save(
        text: Annotated[str, Field(description="Hecho o nota a recordar, autocontenido", min_length=3, max_length=20000)],
        target: Annotated[Literal["context", "shared"], Field(
            description="'context' (default) guarda en el contexto activo; 'shared' lo ve todo contexto")] = "context",
        project: Annotated[str | None, Field(description="Obligatorio en contextos con proyectos (los lista hub_whoami)")] = None,
        tags: Annotated[list[str] | None, Field(description="Etiquetas opcionales (node sets)", max_length=5)] = None,
    ) -> dict[str, Any]:
        """Guarda un hecho en la memoria de largo plazo. Por defecto va al contexto activo.
        Usá target='shared' solo si el dato sirve en todos los contextos. Escribí [[Nombre]] para
        enlazar personas, empresas o proyectos: las notas con el mismo [[Nombre]] quedan conectadas."""
        login = _current_login(settings)
        try:
            name, ds_id = scope().write_id(target=target, project=project)
            clean_tags = [t.strip()[:40] for t in (tags or []) if t.strip()]
            app = _client_app()
            meta = {"source_app": app, "hub_context": ctx.name, "tags": clean_tags,
                    "saved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
            links = wikilinks(text)
            if links:
                meta["links"] = links
            res = await cognee.remember(text, ds_id, node_set=_node_sets(clean_tags, text), metadata=meta)
        except MemoryError_ as exc:
            audit.log("memory_save", login, "rejected", reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("memory_save", login, "error", error=type(exc).__name__)
            raise ToolError("no pude guardar en la memoria (Cognee no respondió)") from exc
        status = res.get("status") if isinstance(res, dict) else None
        safe(ledger.record_save, dataset=name, text=text, context=ctx.name, source_app=app, login=login,
             tags=clean_tags, data_id=_returned_id(res))
        audit.log("memory_save", login, "ok", dataset=name, chars=len(text), app=app)
        return {"saved_to": name, "status": status or "accepted", "source_app": app,
                "note": "la extracción al grafo corre en segundo plano; puede tardar en aparecer en búsquedas"}

    async def _locate(sc: MemoryScope, data_id: str) -> tuple[str, str, dict[str, Any]]:
        """Busca la nota en los datasets PROPIOS del contexto. Nunca toca datasets ajenos."""
        if not _UUID_RE.match(data_id or ""):
            raise MemoryError_("id inválido: usá el id (UUID) que devuelve memory_list")
        for name, ds_id in sc.editable():
            for d in await cognee.list_data(ds_id):
                if str(d.get("id")) == data_id:
                    return name, ds_id, d
        shared_id = sc.datasets.id_of("shared")
        if any(str(d.get("id")) == data_id for d in await cognee.list_data(shared_id)):
            raise MemoryError_(SHARED_EDIT_HINT)
        raise MemoryError_(f"no hay ninguna nota con ese id en el contexto {ctx.name}")

    @mcp.tool(annotations={**READ_ONLY, "title": "Listar notas de memoria"})
    async def memory_list(
        contains: Annotated[str | None, Field(description="Filtrar por texto (sin distinguir mayúsculas)", max_length=200)] = None,
        project: Annotated[str | None, Field(description="Solo contexto side: limitar a un proyecto")] = None,
        include_shared: Annotated[bool, Field(description="Incluir notas de shared (solo lectura)")] = False,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Lista las notas guardadas (más nuevas primero) con su id, dataset, fecha, origen (provenance: app,
        usuario, contexto y fechas de guardado/corrección) y texto.
        Usá el id con memory_update o memory_delete. Las de shared se muestran pero no se pueden editar."""
        login = _current_login(settings)
        needle = (contains or "").strip().lower()
        try:
            sc = scope()
            items: list[dict[str, Any]] = []
            scanned = 0
            for name, ds_id in sc.listable(include_shared=include_shared, project=project):
                for d in await cognee.list_data(ds_id):
                    scanned += 1
                    if scanned > MAX_LIST_SCAN:
                        break
                    text = await cognee.raw_text(ds_id, str(d.get("id"))) or ""
                    if needle and needle not in text.lower():
                        continue
                    it = item_summary(d, name)
                    it["editable"] = name != "shared"
                    prov = safe(ledger.provenance, str(d.get("id")), name, text)
                    if prov:
                        it["provenance"] = prov
                        it["source_app"] = it["source_app"] or prov["source_app"]
                    it["chars"] = len(text)
                    it["text"] = text[:1500]
                    items.append(it)
        except MemoryError_ as exc:
            audit.log("memory_list", login, "rejected", reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("memory_list", login, "error", error=type(exc).__name__)
            raise ToolError("la memoria (Cognee) no respondió; probá de nuevo más tarde") from exc
        items.sort(key=lambda i: str(i.get("created_at") or ""), reverse=True)
        audit.log("memory_list", login, "ok", results=len(items[:limit]))
        return {"context": ctx.name, "total": len(items), "items": items[:limit]}

    @mcp.tool(annotations={"title": "Borrar nota de memoria", "readOnlyHint": False,
                           "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
    async def memory_delete(
        id: Annotated[str, Field(description="id (UUID) de la nota, sacado de memory_list")],
    ) -> dict[str, Any]:
        """Retira UNA nota de la memoria del contexto activo (y lo que el grafo sacó solo de ella).
        Se guarda una copia en el historial: memory_undo la restaura. Solo notas propias del contexto;
        shared no se puede borrar desde acá. Confirmá el id con memory_list."""
        login = _current_login(settings)
        app = _client_app()
        try:
            name, ds_id, item = await _locate(scope(), id)
            old = item_summary(item, name)
            text = await cognee.raw_text(ds_id, id)
            if text is not None:
                safe(ledger.snapshot, data_id=id, dataset=name, context=ctx.name, text=text, tags=old["tags"],
                     action="delete", by=app, created_at=old["created_at"], source_app=old["source_app"])
            await cognee.delete_data(ds_id, id)
        except MemoryError_ as exc:
            audit.log("memory_delete", login, "rejected", data_id=id, reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("memory_delete", login, "error", data_id=id, error=type(exc).__name__)
            raise ToolError("no pude borrar la nota (Cognee no respondió)") from exc
        audit.log("memory_delete", login, "ok", data_id=id, dataset=name, app=app)
        return {"deleted": id, "dataset": name, "undo": "memory_undo con este id la restaura"}

    @mcp.tool(annotations={"title": "Corregir nota de memoria", "readOnlyHint": False,
                           "destructiveHint": True, "openWorldHint": False})
    async def memory_update(
        id: Annotated[str, Field(description="id (UUID) de la nota a corregir, sacado de memory_list")],
        text: Annotated[str, Field(description="Texto nuevo COMPLETO (reemplaza al anterior)", min_length=3, max_length=20000)],
        tags: Annotated[list[str] | None, Field(description="Etiquetas; si no se pasan, se conservan las anteriores", max_length=5)] = None,
    ) -> dict[str, Any]:
        """Corrige una nota propia del contexto en el lugar: conserva su id y su fecha, y el grafo se
        re-extrae solo donde cambió. La versión anterior queda en memory_history (memory_undo la vuelve
        atrás). Shared no se puede editar."""
        login = _current_login(settings)
        app = _client_app()
        try:
            name, ds_id, item = await _locate(scope(), id)
            old = item_summary(item, name)
            clean_tags = [t.strip()[:40] for t in (tags if tags is not None else old["tags"]) if str(t).strip()]
            old_text = await cognee.raw_text(ds_id, id)
            if old_text is None:
                raise MemoryError_("no pude leer el texto actual de la nota; probá de nuevo")
            safe(ledger.snapshot, data_id=id, dataset=name, context=ctx.name, text=old_text, tags=old["tags"],
                 action="update", by=app, created_at=old["created_at"], source_app=old["source_app"])
            new_ns = _node_sets(clean_tags, text) or []
            ns_changed = sorted(new_ns) != sorted(_node_sets(old["tags"], old_text) or [])
            res = await cognee.update(ds_id, id, text, node_set=new_ns if ns_changed else None)
        except MemoryError_ as exc:
            audit.log("memory_update", login, "rejected", data_id=id, reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("memory_update", login, "error", data_id=id, error=type(exc).__name__)
            raise ToolError("no pude corregir la nota (Cognee no respondió); revisá con memory_list") from exc
        safe(ledger.record_change, id, text=text, by=app, tags=clean_tags)
        status = res.get("status") if isinstance(res, dict) else None
        audit.log("memory_update", login, "ok", data_id=id, dataset=name, chars=len(text), app=app, status=status)
        return {"updated": id, "dataset": name, "status": status or "accepted",
                "note": "mismo id; la versión anterior quedó en memory_history"}

    @mcp.tool(annotations={**READ_ONLY, "title": "Historial de una nota"})
    async def memory_history(
        id: Annotated[str, Field(description="id (UUID) de la nota (también sirve el de una nota borrada)")],
    ) -> dict[str, Any]:
        """Muestra el origen de una nota y sus versiones anteriores (correcciones y borrados), la más
        reciente primero. Solo notas de los datasets propios del contexto."""
        login = _current_login(settings)
        if not _UUID_RE.match(id or ""):
            raise ToolError("id inválido: usá el id (UUID) que devuelve memory_list")
        own = {n for n, _ in scope().editable()}
        h = safe(ledger.history, id)
        if not h or h["dataset"] not in own:
            audit.log("memory_history", login, "rejected", data_id=id)
            raise ToolError(f"no hay historial para ese id en el contexto {ctx.name}")
        audit.log("memory_history", login, "ok", data_id=id, versions=len(h["previous_versions"]))
        return h

    @mcp.tool(annotations={"title": "Deshacer cambio en una nota", "readOnlyHint": False,
                           "destructiveHint": True, "openWorldHint": False})
    async def memory_undo(
        id: Annotated[str, Field(description="id (UUID) de la nota corregida o borrada")],
    ) -> dict[str, Any]:
        """Deshace el último cambio de una nota propia: si fue borrada la restaura (con un id nuevo, ver
        memory_list); si fue corregida vuelve a la versión anterior. Repetirlo sigue yendo hacia atrás."""
        login = _current_login(settings)
        app = _client_app()
        try:
            if not _UUID_RE.match(id or ""):
                raise MemoryError_("id inválido: usá el id (UUID) que devuelve memory_list")
            sc = scope()
            own = dict(sc.editable())
            last = ledger.last_version(id)
            if not last or last["dataset"] not in own:
                raise MemoryError_(f"no hay nada para deshacer en esa nota del contexto {ctx.name}")
            ds_id = own[last["dataset"]]
            if last["deleted"]:
                meta = {"source_app": app, "hub_context": ctx.name, "tags": last["tags"], "restored_from": id,
                        "saved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
                res = await cognee.remember(last["text"], ds_id, node_set=_node_sets(last["tags"], last["text"]),
                                            metadata=meta)
                ledger.drop_version(last["vid"])
                ledger.restored(id, text=last["text"], by=app, new_data_id=_returned_id(res))
                action = "restored"
            else:
                name, _ds, item = await _locate(sc, id)
                cur = item_summary(item, name)
                cur_text = await cognee.raw_text(ds_id, id)
                if cur_text is None:
                    raise MemoryError_("no pude leer el texto actual de la nota; probá de nuevo")
                ledger.snapshot(data_id=id, dataset=name, context=ctx.name, text=cur_text, tags=cur["tags"],
                                action="undone", by=app)
                back_ns = _node_sets(last["tags"], last["text"]) or []
                changed = sorted(back_ns) != sorted(_node_sets(cur["tags"], cur_text) or [])
                await cognee.update(ds_id, id, last["text"], node_set=back_ns if changed else None)
                ledger.drop_version(last["vid"])
                ledger.record_change(id, text=last["text"], by=app, tags=last["tags"])
                action = "reverted"
        except MemoryError_ as exc:
            audit.log("memory_undo", login, "rejected", data_id=id, reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("memory_undo", login, "error", data_id=id, error=type(exc).__name__)
            raise ToolError("no pude deshacer el cambio (Cognee o el historial no respondieron)") from exc
        audit.log("memory_undo", login, "ok", data_id=id, action=action, app=app)
        return {"id": id, "action": action, "dataset": last["dataset"], "text": last["text"][:1500]}

    # ------------------------------------------------------------------ skills
    @mcp.tool(annotations={**READ_ONLY, "title": "Listar skills"})
    async def skills_list() -> dict[str, Any]:
        """Lista las skills disponibles en este contexto (nombre y descripción)."""
        login = _current_login(settings)
        items = [{"name": s.name, "description": s.description, "owner": s.owner} for s in skills.visible()]
        audit.log("skills_list", login, "ok", count=len(items))
        return {"context": ctx.name, "skills": items}

    @mcp.tool(annotations={**READ_ONLY, "title": "Leer skill"})
    async def skills_get(
        name: Annotated[str, Field(description="Nombre de la skill (de skills_list)", max_length=64)],
        file: Annotated[str | None, Field(description="Archivo de referencia dentro de la skill (default SKILL.md)",
                                          max_length=200)] = None,
    ) -> dict[str, Any]:
        """Devuelve el contenido de una skill (SKILL.md) o de un archivo de referencia suyo."""
        login = _current_login(settings)
        try:
            content = skills.read(name, file)
            files = skills.files(name)
        except SkillError as exc:
            audit.log("skills_get", login, "rejected", skill=name)
            raise ToolError(str(exc)) from exc
        audit.log("skills_get", login, "ok", skill=name, file=file or "SKILL.md")
        return {"name": name, "file": file or "SKILL.md", "content": content, "files": files}

    @mcp.resource("skill://index", mime_type="application/json", name="skills-index")
    async def skill_index_resource() -> dict[str, Any]:
        """Índice de skills visibles en este contexto."""
        return {"skills": [{"name": s.name, "description": s.description, "uri": f"skill://{s.name}"}
                           for s in skills.visible()]}

    @mcp.resource("skill://{name}", mime_type="text/markdown")
    async def skill_resource(name: str) -> str:
        """Contenido de SKILL.md de una skill visible en este contexto."""
        try:
            return skills.read(name)
        except SkillError as exc:
            raise ResourceError(str(exc)) from exc

    # ------------------------------------------------------------------ secretos
    @mcp.tool(annotations={**READ_ONLY, "title": "Listar secretos"})
    async def secrets_list() -> dict[str, Any]:
        """Lista los secretos utilizables en este contexto: nombre, descripción y hosts permitidos.
        Nunca devuelve valores."""
        login = _current_login(settings)
        audit.log("secrets_list", login, "ok")
        return {"context": ctx.name, "secrets": broker.list()}

    @mcp.tool(annotations={"title": "Request HTTP con secreto", "readOnlyHint": False,
                           "destructiveHint": True, "openWorldHint": True})
    async def secret_http_request(
        secret: Annotated[str, Field(description="Nombre del secreto (de secrets_list)", max_length=100)],
        method: Annotated[Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"], Field()] = "GET",
        url: Annotated[str, Field(description="URL https a un host permitido para ese secreto", max_length=2000)] = "",
        headers: Annotated[dict[str, str] | None, Field(description="Headers extra (no incluyas credenciales)")] = None,
        body: Annotated[str | None, Field(description="Body del request (texto/JSON)", max_length=262144)] = None,
    ) -> dict[str, Any]:
        """Hace un request HTTP con el secreto inyectado del lado del servidor. El valor nunca vuelve:
        la respuesta se redacta. Solo funciona contra los hosts permitidos del secreto."""
        login = _current_login(settings)
        try:
            result = await broker.request(secret, method, url, headers, body)
        except SecretPolicyError as exc:
            audit.log("secret_http_request", login, "rejected", secret=secret, reason=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit.log("secret_http_request", login, "error", secret=secret, error=type(exc).__name__)
            raise ToolError("el request falló (red, timeout o Infisical no disponible)") from exc
        audit.log("secret_http_request", login, "ok", secret=secret, method=method,
                  host=result["host"], status=result["status"])
        return result

    return mcp


def create_app():
    """Punto de entrada ASGI (uvicorn --factory hub_gateway.app:create_app)."""
    logging.basicConfig(level=os.environ.get("HUB_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    mcp = build_server(settings)
    audit = Audit(settings.context, os.path.join(settings.data_dir, "audit.log"))
    return DuplicateRequestIdGuard(
        mcp.http_app(path="/mcp"),
        on_duplicate=lambda ids: audit.log("mcp", None, "remapped", reason="duplicate_request_id", ids=ids),
    )


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run("hub_gateway.app:create_app", factory=True, host=settings.host, port=settings.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
