"""Tests de dashboard/server.py: solo loopback, anti DNS-rebinding, redacción y errores sin detalles."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "dashboard" / "server.py"
spec = importlib.util.spec_from_file_location("mnemos_dashboard", SCRIPT)
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)


@pytest.fixture
def server():
    demo = {"funnel": {"ok": True, "hubs": {}}}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(None, set(), demo))
    port = srv.server_address[1]
    srv.RequestHandlerClass = dash.make_handler(None, {f"127.0.0.1:{port}"}, demo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_serves_status_and_static_with_csp(server):
    r = httpx.get(f"{server}/api/status")
    assert r.status_code == 200 and r.json()["funnel"]["ok"] is True
    page = httpx.get(f"{server}/")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    assert httpx.get(f"{server}/app.js").status_code == 200
    cfg = httpx.get(f"{server}/config.js")
    assert cfg.status_code == 200 and cfg.text.startswith("window.MNEMOS = ")
    names = [c["name"] for c in json.loads(cfg.text.split("=", 1)[1].rstrip().rstrip(";"))["contexts"]]
    assert names == list(dash.CONTEXTS)


def test_rejects_foreign_host_and_path_traversal(server):
    assert httpx.get(f"{server}/api/status", headers={"Host": "evil.example"}).status_code == 421
    assert httpx.get(f"{server}/../server.py").status_code == 404
    assert httpx.get(f"{server}/%2e%2e/server.py").status_code == 404


def test_refuses_all_interfaces(monkeypatch):
    monkeypatch.setattr("sys.argv", ["server.py", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit, match="127.0.0.1"):
        dash.main()


def test_redacts_sensitive_env_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("COGNEE_KEY_SIDE=supersecretvalue123\nTS_TAILNET=tail000\nGITHUB_USER=alex\n")
    monkeypatch.setattr(dash, "ROOT", tmp_path)
    hub = dash.Hub()
    out = hub.redact(json.dumps({"x": "error supersecretvalue123", "user": "alex"}))
    assert "supersecretvalue123" not in out and "[redactado]" in out and "alex" in out


def test_collector_errors_are_sanitized():
    cache = dash.Cache()

    def boom():
        raise httpx.HTTPStatusError("body with token abc", request=httpx.Request("GET", "http://x"),
                                    response=httpx.Response(403))

    val = cache.get("x", 10, boom)
    assert val["ok"] is False and val["error"] == "HTTP 403"
    assert "token" not in json.dumps(val)


# ------------------------------------------------------------------ Explorar
spec_ex = importlib.util.spec_from_file_location("mnemos_explore", SCRIPT.parent / "explore.py")
explore = importlib.util.module_from_spec(spec_ex)
spec_ex.loader.exec_module(explore)


def test_view_datasets_respect_isolation():
    assert explore.view_datasets("personal") == ["personal", "shared"]
    assert explore.view_datasets("side") == ["side_shop", "side_blog", "side_mnemos", "shared"]
    assert "work" not in explore.view_datasets("side")
    assert set(explore.view_datasets("todos")) == {"shared", "work", "personal", "side_shop",
                                                   "side_blog", "side_mnemos"}


def _g(*nodes, edges=()):
    return {"nodes": [{"id": i, "label": lbl, "type": t, "properties": {}} for i, lbl, t in nodes],
            "edges": [{"source": s, "target": d, "label": r} for s, d, r in edges]}


def test_merge_graphs_fuses_common_entities_across_contexts():
    a = _g(("1", "Snowflake", "Entity"), ("2", "Atlas", "Entity"), ("c1", "DocumentChunk_x", "DocumentChunk"),
           edges=[("2", "1", "uses"), ("c1", "2", "contains")])
    b = _g(("9", "snowflake ", "Entity"), ("8", "Alex", "Entity"), edges=[("8", "9", "likes")])
    g = explore.merge_graphs([("work", a), ("personal", b)], "todos")
    by = {n["label"].strip().lower(): n for n in g["nodes"]}
    snow = by["snowflake"]
    assert sorted(snow["datasets"]) == ["personal", "work"] and snow["common"] is True
    assert snow["contexts"] == ["personal", "work"]
    assert by["atlas"]["common"] is False
    # las aristas de ambos datasets apuntan al nodo fusionado
    assert {(e["source"], e["target"]) for e in g["edges"]} >= {("Entity:atlas", "Entity:snowflake"),
                                                                ("Entity:alex", "Entity:snowflake")}
    # los chunks no se fusionan ni cuentan como "en común"
    assert all(not n["common"] for n in g["nodes"] if n["type"] != "Entity")


def test_merge_graphs_in_context_view_counts_datasets_not_contexts():
    a = _g(("1", "Dagster", "Entity"))
    b = _g(("2", "Dagster", "Entity"))
    g = explore.merge_graphs([("side_blog", a), ("side_mnemos", b)], "side")
    assert g["nodes"][0]["common"] is True  # dos proyectos de side comparten el concepto
    g2 = explore.merge_graphs([("side_blog", a), ("side_mnemos", b)], "todos")
    assert g2["nodes"][0]["common"] is False  # en la vista global es el mismo contexto


def test_secrets_view_has_names_and_hosts_only():
    rows = explore.Explorer._secrets("personal")
    assert rows and all(r["context"] == "personal" for r in rows)
    assert all(set(r) == {"name", "context", "description", "hosts", "methods", "header"} for r in rows)
    assert all("format" not in json.dumps(r) for r in rows)  # nada del template de inyección


def test_explore_rejects_unknown_view(tmp_path):
    ex = explore.Explorer({}, dash.err)
    with pytest.raises(ValueError):
        ex.explore("../etc")
    with pytest.raises(ValueError):
        ex.skill("otro", "x")


def test_skill_visibility_uses_gateway_rules(tmp_path):
    root = tmp_path / "mirror"
    for owner, name, extra in (("shared", "revisar-pr", ""), ("personal", "mi-rutina", ""),
                               ("work", "deploy-cb", "metadata:\n  hub-share: side\n")):
        d = root / "skills" / owner / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d {name}\n{extra}---\ncuerpo {name}\n")
    ex = explore.Explorer({}, dash.err)
    ex.skills.ensure = lambda: root
    names = lambda v: {s["name"] for s in ex._skills(v)}  # noqa: E731
    assert names("personal") == {"revisar-pr", "mi-rutina"}
    assert names("side") == {"revisar-pr", "deploy-cb"}  # compartida por hub-share
    assert names("todos") == {"revisar-pr", "mi-rutina", "deploy-cb"}
    assert "cuerpo mi-rutina" in ex.skill("personal", "mi-rutina")["content"]
    with pytest.raises(LookupError):
        ex.skill("work", "mi-rutina")  # skill de otro contexto: no visible


def test_explore_endpoint_is_redacted_and_host_checked(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("COGNEE_KEY_PERSONAL=valor-super-secreto-123\n")
    monkeypatch.setattr(dash, "ROOT", tmp_path)
    hub = dash.Hub()

    class FakeExplorer:
        def explore(self, view, fresh=False):
            return {"view": view, "memories": [{"text": "mi key es valor-super-secreto-123"}]}

        def skill(self, view, name):
            raise LookupError("skill no visible en este contexto")

    hub.explorer = FakeExplorer()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(hub, set(), None))
    port = srv.server_address[1]
    srv.RequestHandlerClass = dash.make_handler(hub, {f"127.0.0.1:{port}"}, None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        r = httpx.get(f"{base}/api/explore?view=personal")
        assert r.status_code == 200 and "valor-super-secreto-123" not in r.text and "[redactado]" in r.text
        assert httpx.get(f"{base}/api/skill?view=personal&name=x").status_code == 404
        assert httpx.get(f"{base}/api/explore?view=personal", headers={"Host": "evil.example"}).status_code == 421
    finally:
        srv.shutdown()


def test_allowed_hosts_loopback_and_tailnet_only():
    base = {"127.0.0.1:8787", "localhost:8787"}
    assert dash.build_allowed_hosts("127.0.0.1", 8787) == base
    got = dash.build_allowed_hosts("127.0.0.1", 8787, 8444, "My-Laptop.tail1234.ts.net.")
    assert got == base | {"my-laptop.tail1234.ts.net:8444"}
    # sin puerto de serve, o con un nombre que no es MagicDNS, no se agrega nada
    assert dash.build_allowed_hosts("127.0.0.1", 8787, 0, "my-laptop.tail1234.ts.net") == base
    for bad in ("evil.example", "my-laptop.tail1234.ts.net.evil.example", "a b.ts.net", ""):
        assert dash.build_allowed_hosts("127.0.0.1", 8787, 8444, bad) == base


def test_tailnet_host_header_accepted_foreign_rejected():
    demo = {"funnel": {"ok": True, "hubs": {}}}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(None, set(), demo))
    port = srv.server_address[1]
    allowed = dash.build_allowed_hosts("127.0.0.1", port, 8444, "my-laptop.tail1234.ts.net")
    srv.RequestHandlerClass = dash.make_handler(None, allowed, demo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/api/status"
        assert httpx.get(url, headers={"Host": "my-laptop.tail1234.ts.net:8444"}).status_code == 200
        assert httpx.get(url, headers={"Host": "MY-LAPTOP.tail1234.ts.net:8444"}).status_code == 200
        assert httpx.get(url, headers={"Host": "my-laptop.tail1234.ts.net"}).status_code == 421
        assert httpx.get(url, headers={"Host": "my-laptop.tail1234.ts.net:8443"}).status_code == 421
    finally:
        srv.shutdown()


def test_explore_view_switch_never_drops_clicks():
    """Regresión 30/09: con una carga en curso, el click en otra vista se descartaba y ese botón quedaba
    muerto (p. ej. 'Todos (admin)', la vista más lenta). La última vista pedida tiene que ganar."""
    js = (SCRIPT.parent / "static" / "explore.js").read_text()
    assert "if (loading) return" not in js
    assert "new AbortController()" in js and "signal: req.signal" in js
