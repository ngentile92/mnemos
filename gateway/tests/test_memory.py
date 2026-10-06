import json

import httpx
import pytest

from hub_gateway.contexts import CONTEXTS
from hub_gateway.memory import CogneeClient, DatasetMap, MemoryError_, MemoryScope, simplify_results
from conftest import DATASETS


@pytest.fixture
def dmap():
    return DatasetMap(DATASETS)


def test_read_ids_per_context(dmap):
    assert MemoryScope(CONTEXTS["personal"], dmap).read_ids() == [DATASETS["personal"], DATASETS["shared"]]
    assert MemoryScope(CONTEXTS["work"], dmap).read_ids(include_shared=False) == [DATASETS["work"]]
    side = MemoryScope(CONTEXTS["side"], dmap)
    assert set(side.read_ids()) == {DATASETS[k] for k in ("side_shop", "side_blog", "side_mnemos", "shared")}
    assert side.read_ids(project="blog", include_shared=False) == [DATASETS["side_blog"]]


def test_no_context_can_reach_foreign_datasets(dmap):
    for name, spec in CONTEXTS.items():
        sc = MemoryScope(spec, dmap)
        foreign = {DATASETS[d] for c, s in CONTEXTS.items() if c != name for d in s.own_dataset_names}
        assert not (set(sc.read_ids()) & foreign)
        assert not (sc.allowed_ids() & foreign)


def test_write_targets(dmap):
    assert MemoryScope(CONTEXTS["personal"], dmap).write_id() == ("personal", DATASETS["personal"])
    assert MemoryScope(CONTEXTS["personal"], dmap).write_id("shared") == ("shared", DATASETS["shared"])
    side = MemoryScope(CONTEXTS["side"], dmap)
    with pytest.raises(MemoryError_, match="project"):
        side.write_id()
    assert side.write_id(project="mnemos") == ("side_mnemos", DATASETS["side_mnemos"])


@pytest.mark.parametrize("project", ["work", "personal", "../shared", "blog "])
def test_invalid_projects_rejected(dmap, project):
    with pytest.raises(MemoryError_):
        MemoryScope(CONTEXTS["side"], dmap).write_id(project=project)
    with pytest.raises(MemoryError_):
        MemoryScope(CONTEXTS["personal"], dmap).read_ids(project=project)


def test_invalid_target(dmap):
    with pytest.raises(MemoryError_):
        MemoryScope(CONTEXTS["personal"], dmap).write_id(target="work")


def test_simplify_filters_foreign_results():
    allowed = {DATASETS["personal"], DATASETS["shared"]}
    raw = [
        {"source": "graph", "text": "dato personal", "dataset_id": DATASETS["personal"], "dataset_name": "personal"},
        {"source": "graph", "text": "dato work", "dataset_id": DATASETS["work"], "dataset_name": "work"},
        {"source": "system", "text": "memoria calentando"},
    ]
    out = simplify_results(raw, allowed)
    texts = [r["text"] for r in out]
    assert "dato personal" in texts and "memoria calentando" in texts
    assert "dato work" not in texts


async def test_recall_payload_uses_ids_and_no_llm():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["headers"] = dict(request.headers)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=[])

    c = CogneeClient("http://cognee:8000", "k-123", transport=httpx.MockTransport(handler))
    await c.recall("hola", [DATASETS["personal"]], top_k=5)
    assert seen["path"] == "/api/v1/recall"
    assert seen["headers"]["x-api-key"] == "k-123"
    body = seen["json"]
    assert body["dataset_ids"] == [DATASETS["personal"]]
    assert body["only_context"] is True and body["scope"] == "graph"
    assert "datasets" not in body


async def test_recall_refuses_unscoped():
    c = CogneeClient("http://cognee:8000", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(MemoryError_):
        await c.recall("hola", [])


async def test_remember_multipart():
    seen = {}

    def handler(request: httpx.Request):
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "running"})

    c = CogneeClient("http://cognee:8000", "k", transport=httpx.MockTransport(handler))
    res = await c.remember("Acme Corp es cliente", DATASETS["work"], node_set=["crm"])
    assert res["status"] == "running"
    assert seen["ctype"].startswith("multipart/form-data")
    assert 'name="datasetId"' in seen["body"] and DATASETS["work"] in seen["body"]
    assert 'name="node_set"' in seen["body"] and 'name="datasetName"' not in seen["body"]
    assert 'name="external_metadata"' not in seen["body"]  # sin metadata no se manda el campo


async def test_remember_sends_external_metadata_one_entry():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "running"})

    c = CogneeClient("http://cognee:8000", "k", transport=httpx.MockTransport(handler))
    await c.remember("nota", DATASETS["personal"], metadata={"source_app": "claude-ai", "hub_context": "personal",
                                                             "node_set": ["no"], "tags": [], "x": None})
    body = seen["body"]
    assert 'name="external_metadata"' in body
    raw = body.split('name="external_metadata"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
    assert json.loads(raw) == [{"source_app": "claude-ai", "hub_context": "personal"}]


async def test_cognee_403_becomes_memory_error():
    c = CogneeClient("http://c", "k", transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})))
    with pytest.raises(MemoryError_):
        await c.recall("x", [DATASETS["personal"]])


async def test_empty_dataset_is_empty_result():
    body = {"detail": "No searchable memory in dataset 'side_blog': no data has been added. [NoDataError]"}
    c = CogneeClient("http://c", "k", transport=httpx.MockTransport(lambda r: httpx.Response(404, json=body)))
    assert await c.recall("x", [DATASETS["side_blog"]]) == []
