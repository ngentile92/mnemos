import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hub_gateway.app import build_server
from hub_gateway.audit import Audit
from hub_gateway.config import ConfigError, Settings
from hub_gateway.memory import CogneeClient, DatasetMap
from hub_gateway.secrets import SecretBroker, load_policy
from hub_gateway.skills import SkillIndex
from conftest import DATASETS


def dev_env(monkeypatch, tmp_path, context, **extra):
    monkeypatch.setenv("HUB_CONTEXT", context)
    monkeypatch.setenv("HUB_DEV_NO_AUTH", "1")
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


NOTE_IDS = {
    "personal": "11111111-1111-4111-8111-111111111111",
    "shared": "22222222-2222-4222-8222-222222222222",
    "work": "33333333-3333-4333-8333-333333333333",
}


class CogneeRecorder:
    def __init__(self):
        self.recalls = []
        self.remembers = []
        self.deletes = []
        self.patches = []
        # una nota por dataset con texto propio
        self.items = {DATASETS[n]: [{"id": i, "createdAt": f"2026-09-29T1{k}:00:00", "mimeType": "text/plain",
                                     "externalMetadata": {"source_app": "test", "tags": ["t"]}}]
                      for k, (n, i) in enumerate(NOTE_IDS.items())}
        self.texts = {i: f"nota de {n} sobre orion" for n, i in NOTE_IDS.items()}

    def handler(self, request: httpx.Request):
        parts = request.url.path.strip("/").split("/")
        if parts[:3] == ["api", "v1", "datasets"] and len(parts) >= 5 and parts[4] == "data":
            ds = parts[3]
            if len(parts) == 5 and request.method == "GET":
                return httpx.Response(200, json=self.items.get(ds, []))
            if len(parts) == 7 and parts[6] == "raw":
                return httpx.Response(200, text=self.texts.get(parts[5], ""))
            if len(parts) == 6 and request.method == "DELETE":
                self.deletes.append((ds, parts[5]))
                return httpx.Response(200, json={})
        if request.url.path == "/api/v1/recall":
            body = json.loads(request.content)
            self.recalls.append(body)
            # Cognee "malicioso": devuelve también un dato de otro dataset; el gateway debe descartarlo.
            return httpx.Response(200, json=[
                {"source": "graph", "text": f"hit {d}", "dataset_id": d} for d in body["dataset_ids"]
            ] + [{"source": "graph", "text": "FUGA", "dataset_id": DATASETS["work"]}])
        if request.url.path == "/api/v1/remember":
            self.remembers.append(request.content.decode())
            return httpx.Response(200, json={"status": "running"})
        if request.url.path == "/api/v1/update" and request.method == "PATCH":
            did = request.url.params["data_id"]
            body = request.content.decode()
            self.patches.append((request.url.params["dataset_id"], did, body))
            self.texts[did] = body.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
            return httpx.Response(200, json={"status": "incremental" if "node_set" not in body else "full_rebuild"})
        return httpx.Response(404)


@pytest.fixture
def make_server(monkeypatch, tmp_path, skills_repo, policy_file):
    def _make(context):
        dev_env(monkeypatch, tmp_path, context)
        rec = CogneeRecorder()
        settings = Settings.from_env()
        server = build_server(
            settings,
            cognee=CogneeClient("http://cognee:8000", "k", transport=httpx.MockTransport(lambda r: rec.handler(r))),
            datasets=DatasetMap(DATASETS),
            skills=SkillIndex(skills_repo, context),
            broker=SecretBroker(load_policy(str(policy_file), context), None),
            audit=Audit(context, str(tmp_path / "audit.log")),
        )
        return server, rec
    return _make


def data(result):
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


async def test_tool_surface_has_no_shell(make_server):
    server, _ = make_server("personal")
    async with Client(server) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == {"hub_whoami", "memory_search", "memory_save", "memory_list", "memory_update",
                          "memory_delete", "memory_history", "memory_undo", "skills_list", "skills_get", "secrets_list", "secret_http_request"}
    for name in tools:
        assert not any(w in name for w in ("forget", "shell", "exec", "prune"))
    for ro in ("hub_whoami", "memory_search", "memory_list", "memory_history", "skills_list", "skills_get", "secrets_list"):
        assert tools[ro].annotations.read_only_hint is True
    assert tools["memory_save"].annotations.read_only_hint is False
    for destructive in ("memory_delete", "memory_update", "memory_undo"):
        assert tools[destructive].annotations.destructive_hint is True


async def test_whoami(make_server):
    server, _ = make_server("side")
    async with Client(server) as c:
        who = data(await c.call_tool("hub_whoami", {}))
    assert who["context"] == "side" and who["projects"] == ["shop", "blog", "mnemos"]


async def test_memory_search_scoped_and_filtered(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_search", {"query": "qué sé de Ben?"}))
    assert rec.recalls[0]["dataset_ids"] == [DATASETS["personal"], DATASETS["shared"]]
    texts = [r["text"] for r in out["results"]]
    assert "FUGA" not in texts and len(texts) == 2


async def test_memory_search_without_shared(make_server):
    server, rec = make_server("work")
    async with Client(server) as c:
        await c.call_tool("memory_search", {"query": "algo", "include_shared": False})
    assert rec.recalls[0]["dataset_ids"] == [DATASETS["work"]]


async def test_memory_save_default_and_shared(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        r1 = data(await c.call_tool("memory_save", {"text": "Me gusta el café"}))
        r2 = data(await c.call_tool("memory_save", {"text": "Uso Python 3.12", "target": "shared"}))
    assert r1["saved_to"] == "personal" and r2["saved_to"] == "shared"
    assert DATASETS["personal"] in rec.remembers[0] and DATASETS["shared"] in rec.remembers[1]
    # origen de la memoria (app cliente del initialize de MCP + contexto) viaja como external_metadata
    assert 'name="external_metadata"' in rec.remembers[0] and '"hub_context": "personal"' in rec.remembers[0]
    assert r1["source_app"]  # el Client de fastmcp se presenta con un nombre
    assert f'"source_app": "{r1["source_app"]}"' in rec.remembers[0]


async def test_memory_save_side_requires_project(make_server):
    server, rec = make_server("side")
    async with Client(server) as c:
        with pytest.raises(ToolError, match="project"):
            await c.call_tool("memory_save", {"text": "algo de side"})
        r = data(await c.call_tool("memory_save", {"text": "deploy en fly", "project": "shop"}))
    assert r["saved_to"] == "side_shop"
    assert DATASETS["side_shop"] in rec.remembers[0]


async def test_memory_save_cannot_target_other_context(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        with pytest.raises(ToolError):
            await c.call_tool("memory_save", {"text": "x" * 10, "target": "work"})
        with pytest.raises(ToolError):
            await c.call_tool("memory_save", {"text": "x" * 10, "project": "blog"})
    assert rec.remembers == []


async def test_skills_filtered_by_context(make_server):
    server, _ = make_server("personal")
    async with Client(server) as c:
        listed = {s["name"] for s in data(await c.call_tool("skills_list", {}))["skills"]}
        assert listed == {"revisar-pr", "rutina-gimnasio"}
        with pytest.raises(ToolError, match="no encontrada"):
            await c.call_tool("skills_get", {"name": "review-cliente"})
        got = data(await c.call_tool("skills_get", {"name": "rutina-gimnasio"}))
        assert "Cuerpo" in got["content"]
        res = await c.read_resource("skill://rutina-gimnasio")
        assert "Cuerpo" in res[0].text
        idx = await c.read_resource("skill://index")
        assert "review-cliente" not in idx[0].text


async def test_secrets_list_shows_only_context_policy(make_server):
    server, _ = make_server("work")
    async with Client(server) as c:
        listed = data(await c.call_tool("secrets_list", {}))["secrets"]
        assert [s["name"] for s in listed] == ["HUBSPOT_TOKEN"]
        with pytest.raises(ToolError, match="no existe"):
            await c.call_tool("secret_http_request", {"secret": "GITHUB_PAT", "url": "https://api.github.com/user"})


async def test_audit_log_has_no_content(make_server, tmp_path):
    server, _ = make_server("personal")
    async with Client(server) as c:
        await c.call_tool("memory_save", {"text": "dato super privado 123"})
    log = (tmp_path / "audit.log").read_text()
    assert "memory_save" in log and "super privado" not in log


# ---------------------------------------------------------------- configuración y auth

def test_dev_mode_refuses_public_url(monkeypatch, tmp_path):
    dev_env(monkeypatch, tmp_path, "personal", HUB_PUBLIC_URL="https://hub-personal.tailnet.ts.net")
    with pytest.raises(ConfigError, match="localhost"):
        Settings.from_env()


def test_prod_mode_requires_oauth_and_allowlist(monkeypatch):
    monkeypatch.setenv("HUB_CONTEXT", "personal")
    monkeypatch.delenv("HUB_DEV_NO_AUTH", raising=False)
    with pytest.raises(ConfigError, match="OAuth"):
        Settings.from_env()


def test_invalid_context(monkeypatch):
    monkeypatch.setenv("HUB_CONTEXT", "marte")
    with pytest.raises(ConfigError):
        Settings.from_env()


@pytest.fixture
def prod_app(monkeypatch, tmp_path, skills_repo, policy_file, datasets_file):
    from cryptography.fernet import Fernet

    monkeypatch.delenv("HUB_DEV_NO_AUTH", raising=False)
    env = {
        "HUB_CONTEXT": "personal",
        "HUB_PUBLIC_URL": "https://hub-personal.example.ts.net",
        "HUB_ALLOWED_GITHUB_LOGINS": "octocat",
        "GITHUB_CLIENT_ID": "Iv1.test",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "HUB_JWT_SIGNING_KEY": "a" * 64,
        "HUB_STORAGE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "HUB_DATA_DIR": str(tmp_path / "data"),
        "HUB_SKILLS_ROOT": str(skills_repo),
        "HUB_SECRET_POLICY_FILE": str(policy_file),
        "HUB_DATASETS_FILE": str(datasets_file),
        "COGNEE_API_KEY": "test-key",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    server = build_server(Settings.from_env())
    return server.http_app(path="/mcp")


async def test_prod_mcp_requires_token(prod_app):
    async with prod_app.router.lifespan_context(prod_app):
        transport = httpx.ASGITransport(app=prod_app)
        async with httpx.AsyncClient(transport=transport, base_url="https://hub-personal.example.ts.net") as c:
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Accept": "application/json, text/event-stream"})
            assert r.status_code == 401
            assert "resource_metadata" in r.headers.get("www-authenticate", "")
            prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
            assert prm["resource"].rstrip("/") == "https://hub-personal.example.ts.net/mcp"
            asm = (await c.get("/.well-known/oauth-authorization-server")).json()
            assert "S256" in asm["code_challenge_methods_supported"]
            assert asm.get("registration_endpoint")
            assert (await c.get("/healthz")).status_code == 200


def test_owner_check():
    from types import SimpleNamespace

    from hub_gateway.auth import make_owner_check

    check = make_owner_check(frozenset({"octocat"}))
    tok = lambda login: SimpleNamespace(token=SimpleNamespace(claims={"login": login}))
    assert check(tok("octocat")) and check(tok("OctoCat"))
    assert not check(tok("otro-usuario"))
    assert not check(SimpleNamespace(token=None))


# ---------------------------------------------------------------- borrar / corregir memoria

async def test_memory_list_scoped_with_ids(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        own = data(await c.call_tool("memory_list", {}))
        both = data(await c.call_tool("memory_list", {"include_shared": True, "contains": "ORION"}))
        none = data(await c.call_tool("memory_list", {"contains": "no-existe"}))
    assert [i["id"] for i in own["items"]] == [NOTE_IDS["personal"]]
    assert own["items"][0]["editable"] is True and own["items"][0]["source_app"] == "test"
    assert {i["dataset"]: i["editable"] for i in both["items"]} == {"personal": True, "shared": False}
    assert none["items"] == []
    assert all(i["id"] != NOTE_IDS["work"] for i in both["items"])


async def test_memory_delete_own_note(make_server, tmp_path):
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_delete", {"id": NOTE_IDS["personal"]}))
    assert out["deleted"] == NOTE_IDS["personal"] and out["dataset"] == "personal"
    assert rec.deletes == [(DATASETS["personal"], NOTE_IDS["personal"])]
    log = (tmp_path / "audit.log").read_text()
    assert "memory_delete" in log and "orion" not in log


async def test_memory_delete_refuses_shared_foreign_and_bad_ids(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        with pytest.raises(ToolError, match="hub-admin"):
            await c.call_tool("memory_delete", {"id": NOTE_IDS["shared"]})
        with pytest.raises(ToolError, match="no hay ninguna nota"):
            await c.call_tool("memory_delete", {"id": NOTE_IDS["work"]})  # nota de otro contexto
        with pytest.raises(ToolError, match="id inválido"):
            await c.call_tool("memory_delete", {"id": "../../datasets"})
        with pytest.raises(ToolError, match="hub-admin"):
            await c.call_tool("memory_update", {"id": NOTE_IDS["shared"], "text": "cambio"})
    assert rec.deletes == [] and rec.remembers == []


async def test_memory_update_patches_in_place(make_server):
    server, rec = make_server("personal")
    pid = NOTE_IDS["personal"]
    async with Client(server) as c:
        out = data(await c.call_tool("memory_update", {"id": pid, "text": "versión corregida"}))
        hist = data(await c.call_tool("memory_history", {"id": pid}))
    assert out["updated"] == pid and out["status"] == "incremental"
    assert rec.patches[0][:2] == (DATASETS["personal"], pid) and "node_set" not in rec.patches[0][2]
    assert rec.remembers == [] and rec.deletes == []
    assert rec.texts[pid] == "versión corregida"
    assert hist["previous_versions"][0]["text"] == "nota de personal sobre orion"
    assert hist["previous_versions"][0]["action"] == "update"


async def test_memory_update_tags_change_sends_node_set(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        out = data(await c.call_tool("memory_update", {"id": NOTE_IDS["personal"], "text": "otra", "tags": ["n"]}))
    assert out["status"] == "full_rebuild" and "node_set" in rec.patches[0][2]


async def test_memory_undo_walks_back_corrections(make_server):
    server, rec = make_server("personal")
    pid = NOTE_IDS["personal"]
    async with Client(server) as c:
        await c.call_tool("memory_update", {"id": pid, "text": "ver2"})
        await c.call_tool("memory_update", {"id": pid, "text": "ver3"})
        assert data(await c.call_tool("memory_undo", {"id": pid}))["action"] == "reverted"
        assert rec.texts[pid] == "ver2"
        await c.call_tool("memory_undo", {"id": pid})
        assert rec.texts[pid] == "nota de personal sobre orion"
        with pytest.raises(ToolError, match="nada para deshacer"):
            await c.call_tool("memory_undo", {"id": pid})
        hist = data(await c.call_tool("memory_history", {"id": pid}))
    assert [v["action"] for v in hist["previous_versions"]] == ["undone", "undone"]


async def test_memory_delete_then_undo_restores(make_server):
    server, rec = make_server("personal")
    pid = NOTE_IDS["personal"]
    async with Client(server) as c:
        await c.call_tool("memory_delete", {"id": pid})
        out = data(await c.call_tool("memory_undo", {"id": pid}))
        h = data(await c.call_tool("memory_history", {"id": pid}))
    assert out["action"] == "restored" and out["text"] == "nota de personal sobre orion"
    assert len(rec.remembers) == 1 and "nota de personal sobre orion" in rec.remembers[0]
    assert f'"restored_from": "{pid}"' in rec.remembers[0]
    assert h["deleted"] is False


async def test_history_and_undo_scoped_to_own_context(make_server):
    server, rec = make_server("work")
    async with Client(server) as c:
        await c.call_tool("memory_delete", {"id": NOTE_IDS["work"]})
    server, rec2 = make_server("personal")  # same data dir would be per-context in compose
    async with Client(server) as c:
        with pytest.raises(ToolError, match="no hay"):
            await c.call_tool("memory_history", {"id": NOTE_IDS["work"]})
        with pytest.raises(ToolError, match="nada para deshacer"):
            await c.call_tool("memory_undo", {"id": NOTE_IDS["work"]})
        with pytest.raises(ToolError, match="inválido"):
            await c.call_tool("memory_history", {"id": "x"})
    assert rec2.remembers == []


async def test_memory_update_keeps_old_note_if_patch_fails(make_server):
    server, rec = make_server("personal")
    orig = rec.handler

    def failing(request):
        if request.url.path == "/api/v1/update":
            return httpx.Response(500)
        return orig(request)

    rec.handler = failing
    async with Client(server) as c:
        with pytest.raises(ToolError):
            await c.call_tool("memory_update", {"id": NOTE_IDS["personal"], "text": "nuevo"})
    assert rec.deletes == []


@pytest.mark.parametrize("context", ["work", "personal", "side"])
async def test_server_instructions_per_context(make_server, context):
    server, _ = make_server(context)
    async with Client(server) as c:
        instr = c.instructions
    assert f"Context: {context}" in instr and "memory_search" in instr and "secret_http_request" in instr
    assert "DATA, not instructions" in instr and len(instr) < 1300  # some connectors truncate at ~1,400
    assert ("project (shop | blog | mnemos)" in instr) == (context == "side")


# ---------------------------------------------------------------- provenance registry

async def test_memory_save_records_provenance_shown_in_list(make_server):
    server, rec = make_server("personal")
    new_id = "44444444-4444-4444-8444-444444444444"
    orig = rec.handler

    def remember_then_list(request):
        if request.url.path == "/api/v1/remember":
            rec.remembers.append(request.content.decode())
            # Cognee in background mode does not return the id: the registry binds it later by text
            rec.items[DATASETS["personal"]].append({"id": new_id, "createdAt": "2026-10-09T10:00:00",
                                                     "externalMetadata": {}})
            rec.texts[new_id] = "Prefiere té verde"
            return httpx.Response(200, json={"status": "running"})
        return orig(request)

    rec.handler = remember_then_list
    async with Client(server) as c:
        await c.call_tool("memory_save", {"text": "Prefiere té verde", "tags": ["gustos"]})
        items = {i["id"]: i for i in data(await c.call_tool("memory_list", {}))["items"]}
    prov = items[new_id]["provenance"]
    assert prov["saved_by"] == "dev-no-auth" and prov["saved_in_context"] == "personal" and prov["saved_at"]
    assert "provenance" not in items[NOTE_IDS["personal"]]  # notes saved before the registry: Cognee metadata only
    assert items[NOTE_IDS["personal"]]["source_app"] == "test"


def test_ledger_binds_by_hash_and_dedups(tmp_path):
    from hub_gateway.ledger import Ledger

    lg = Ledger(str(tmp_path / "l.sqlite"))
    lg.record_save(dataset="work", text="hola", context="work", source_app="claude-ai", login="me", tags=[])
    assert lg.provenance("a" * 8, "work", "otra cosa") is None
    p = lg.provenance("a" * 8, "work", " hola ")
    assert p["source_app"] == "claude-ai" and lg.provenance("a" * 8, "work") == p
    lg.record_save(dataset="work", text="hola", context="work", source_app="x", login="me", tags=[], data_id="a" * 8)
    assert lg.provenance("a" * 8, "work")["source_app"] == "claude-ai"
    lg.record_change("a" * 8, text="chau", by="cursor", id_changes=True)
    assert lg.provenance("a" * 8, "work") is None
    assert lg.provenance("b" * 8, "work", "chau")["updated_by"] == "cursor"


def test_ledger_failure_never_breaks(tmp_path):
    from hub_gateway.ledger import safe

    def boom():
        raise RuntimeError("disk full")

    assert safe(boom) is None


# ---------------------------------------------------------------- [[wikilinks]] -> node sets

def test_wikilinks_parsing():
    from hub_gateway.app import _node_sets, wikilinks

    t = "Reunión con [[Ben Turner]] y [[ben  turner]] de [[Acme|la empresa]]; [[ ]] [[a\nb]] [[X]][[Y]][[Z]][[W]]"
    assert wikilinks(t) == ["Ben Turner", "Acme", "X", "Y", "Z"]
    assert _node_sets(["acme"], "[[Acme]] y [[Bob]]") == ["acme", "Bob"]
    assert _node_sets([], "sin enlaces") is None


async def test_memory_save_sends_links_as_node_sets(make_server):
    server, rec = make_server("personal")
    async with Client(server) as c:
        await c.call_tool("memory_save", {"text": "Almuerzo con [[Ben Turner]]", "tags": ["gente"]})
    body = rec.remembers[0]
    assert body.count('name="node_set"') == 2 and "Ben Turner" in body and '"links": ["Ben Turner"]' in body


async def test_memory_update_sends_node_set_when_links_change(make_server):
    server, rec = make_server("personal")
    pid = NOTE_IDS["personal"]
    async with Client(server) as c:
        await c.call_tool("memory_update", {"id": pid, "text": "nota de personal sobre orion, corregida"})
        await c.call_tool("memory_update", {"id": pid, "text": "ahora con [[Orion]]"})
    assert "node_set" not in rec.patches[0][2]
    assert "node_set" in rec.patches[1][2] and "Orion" in rec.patches[1][2]


async def test_skills_list_and_get_expose_gbrain_fields(make_server, skills_repo):
    d = skills_repo / "skills" / "shared" / "query"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: query\ndescription: Q\ntriggers: [\"who is\"]\ntools: [search, exec]\n"
                                "mutating: false\n---\nbody\n", encoding="utf-8")
    server, _ = make_server("personal")
    async with Client(server) as c:
        listed = {s["name"]: s for s in data(await c.call_tool("skills_list", {}))["skills"]}
        got = data(await c.call_tool("skills_get", {"name": "query"}))
        plain = data(await c.call_tool("skills_get", {"name": "revisar-pr"}))
    assert listed["query"]["triggers"] == ["who is"] and listed["query"]["mutating"] is False
    assert "triggers" not in listed["revisar-pr"]
    assert got["tool_equivalents"] == {"search": "memory_search", "exec": None}
    assert "tool_equivalents" not in plain
