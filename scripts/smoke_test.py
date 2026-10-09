#!/usr/bin/env python3
"""Smoke tests end-to-end del hub.

Modos:
  --dev    contra compose.dev.yaml (gateways sin auth en 127.0.0.1:18101-18103, Cognee en :18000).
           Prueba aislamiento real de memoria (escribe y busca hechos marcados), skills por contexto,
           policy de secretos y el 403 de Cognee entre contextos.
  --quick  contra producción: health de Cognee y, por cada hostname público, 401 en /mcp sin token y
           metadata OAuth correcta. No necesita credenciales.

Requiere: pip install "fastmcp==4.0.*" httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
# --dev: el stack de compose.dev.yaml siempre trae work / personal / side.
DEV_CONTEXTS = ["work", "personal", "side"]
DEV_PORTS = {"work": 18101, "personal": 18102, "side": 18103}
CONTEXTS = DEV_CONTEXTS


def _prod_contexts() -> list[str]:
    """--quick: los contextos de TU instancia (config/contexts.yaml, vía hub_gateway.contexts)."""
    sys.path.insert(0, str(ROOT / "gateway" / "src"))
    try:
        from hub_gateway.contexts import CONTEXTS as ctxs
    except ImportError:  # sin pyyaml: leer contexts.yaml a mano no vale la pena; usar los default
        return DEV_CONTEXTS
    return list(ctxs)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def dev(cognee_url: str, state_dir: Path, wait_s: int) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    run = uuid.uuid4().hex[:8]
    marks = {c: f"marca-{c}-{run}" for c in CONTEXTS}
    shared_mark = f"marca-shared-{run}"
    clients = {c: Client(f"http://127.0.0.1:{DEV_PORTS[c]}/mcp") for c in CONTEXTS}

    print("1) superficie de tools y contexto")
    for c, cl in clients.items():
        async with cl:
            tools = {t.name for t in await cl.list_tools()}
            check(not any(w in t for t in tools for w in ("delete", "forget", "shell", "exec")),
                  f"{c}: sin tools de borrado/shell ({len(tools)} tools)")
            who = payload(await cl.call_tool("hub_whoami", {}))
            check(who["context"] == c, f"{c}: hub_whoami devuelve el contexto correcto")

    print("2) escribir hechos marcados")
    facts = {
        "work": f"El cliente Acme Corp de Work pidió una POC de datos. Código {marks['work']}.",
        "personal": f"Alex prefiere entrenar temprano los martes. Código {marks['personal']}.",
        "side": f"Shop se despliega con Docker. Código {marks['side']}.",
    }
    for c, text in facts.items():
        args = {"text": text}
        if c == "side":
            args["project"] = "shop"
        async with clients[c]:
            res = payload(await clients[c].call_tool("memory_save", args))
            check(res["saved_to"] in (c, "side_shop"), f"{c}: memory_save → {res['saved_to']}")
    async with clients["personal"]:
        res = payload(await clients["personal"].call_tool(
            "memory_save", {"text": f"Alex usa Python 3.12 en todos sus proyectos. Código {shared_mark}.",
                            "target": "shared"}))
        check(res["saved_to"] == "shared", "personal: memory_save target=shared → shared")

    print(f"3) buscar (esperando hasta {wait_s}s a que termine la ingesta en segundo plano)")

    async def search_text(c: str, query: str, **kw) -> str:
        async with clients[c]:
            res = payload(await clients[c].call_tool("memory_search", {"query": query, "top_k": 30, **kw}))
        return json.dumps(res, ensure_ascii=False)

    deadline = time.time() + wait_s
    while time.time() < deadline:
        texts = {c: await search_text(c, "código marca") for c in CONTEXTS}
        if all(marks[c] in texts[c] for c in CONTEXTS) and all(shared_mark in texts[c] for c in CONTEXTS):
            break
        await asyncio.sleep(5)
    for c in CONTEXTS:
        check(marks[c] in texts[c], f"{c}: encuentra su propio hecho")
        check(shared_mark in texts[c], f"{c}: encuentra el hecho compartido")
        for other in CONTEXTS:
            if other != c:
                check(marks[other] not in texts[c], f"{c}: NO ve el hecho de {other}")
    no_shared = await search_text("personal", "código marca", include_shared=False)
    check(shared_mark not in no_shared and marks["personal"] in no_shared,
          "personal: include_shared=false excluye shared")
    blog_only = await search_text("side", "código marca", project="blog", include_shared=False)
    check(marks["side"] not in blog_only, "side: project=blog no ve hechos de shop")

    print("4) validaciones de escritura")
    async with clients["side"]:
        try:
            await clients["side"].call_tool("memory_save", {"text": "sin proyecto"})
            check(False, "side: memory_save sin project debería fallar")
        except ToolError:
            check(True, "side: memory_save sin project → rechazado")
    async with clients["personal"]:
        try:
            await clients["personal"].call_tool("memory_save", {"text": "intento cruzado", "project": "blog"})
            check(False, "personal: project=blog debería fallar")
        except ToolError:
            check(True, "personal: no puede escribir en datasets de side")

    print("5) Cognee directo: la API key de un contexto no lee datasets ajenos")
    ds = json.loads((state_dir / "cognee-datasets.json").read_text())["datasets"]
    for c, other in (("personal", "work"), ("work", "personal"), ("side", "personal")):
        key = (state_dir / f"{c}.env").read_text().split("=", 1)[1].strip()
        r = httpx.post(f"{cognee_url}/api/v1/recall", headers={"X-Api-Key": key}, timeout=60,
                       json={"query": "marca", "dataset_ids": [ds[other]], "search_type": "CHUNKS",
                             "only_context": True, "scope": "graph"})
        check(r.status_code == 403, f"ctx-{c} → dataset {other}: HTTP {r.status_code} (esperado 403)")
    key = (state_dir / "personal.env").read_text().split("=", 1)[1].strip()
    r = httpx.post(f"{cognee_url}/api/v1/remember", headers={"X-Api-Key": key}, timeout=60,
                   files=[("raw_data", (None, "escritura cruzada")), ("datasetId", (None, ds["work"]))])
    check(r.status_code == 403, f"ctx-personal escribe en work: HTTP {r.status_code} (esperado 403)")

    print("6) skills por contexto (fixture dev/skills-fixture)")
    expected = {
        "work": {"fixture-compartida", "fixture-work", "fixture-work-compartida-side"},
        "personal": {"fixture-compartida", "fixture-personal"},
        "side": {"fixture-compartida", "fixture-side", "fixture-work-compartida-side"},
    }
    for c in CONTEXTS:
        async with clients[c]:
            names = {s["name"] for s in payload(await clients[c].call_tool("skills_list", {}))["skills"]}
        check(names == expected[c], f"{c}: skills visibles = {sorted(names)}")
    async with clients["personal"]:
        try:
            await clients["personal"].call_tool("skills_get", {"name": "fixture-work"})
            check(False, "personal: no debería leer fixture-work")
        except ToolError:
            check(True, "personal: skills_get de una skill de work → no encontrada")

    print("7) secretos: solo nombres, host allowlist")
    async with clients["personal"]:
        listed = payload(await clients["personal"].call_tool("secrets_list", {}))["secrets"]
        check(all(set(s) == {"name", "description", "allowed_hosts", "allowed_methods", "injected_as"} for s in listed),
              f"personal: secrets_list solo metadatos ({[s['name'] for s in listed]})")
        if listed:
            try:
                await clients["personal"].call_tool("secret_http_request", {
                    "secret": listed[0]["name"], "method": "GET", "url": "https://example.com/robar"})
                check(False, "host no permitido debería fallar")
            except ToolError as exc:
                check("no permitido" in str(exc), "secret_http_request a example.com → rechazado por allowlist")


def _tailnet_from_env() -> str | None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        if line.startswith("TS_TAILNET="):
            val = line.split("=", 1)[1].split("#", 1)[0].strip()
            return val if val and not val.startswith("__") else None
    return None


async def quick(cognee_url: str, tailnet: str | None) -> None:
    print("1) Cognee")
    try:
        r = httpx.get(f"{cognee_url}/health", timeout=10)
        check(r.status_code == 200, f"Cognee /health HTTP {r.status_code}")
    except httpx.HTTPError as exc:
        check(False, f"Cognee no responde: {exc}")
    if not tailnet:
        print("   (sin TS_TAILNET: salteo los endpoints públicos)")
        return
    print("2) endpoints públicos (Funnel)")
    for c in CONTEXTS:
        base = f"https://hub-{c}.{tailnet}.ts.net"
        try:
            r = httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Accept": "application/json, text/event-stream"}, timeout=20)
            check(r.status_code == 401 and "resource_metadata" in r.headers.get("www-authenticate", ""),
                  f"{c}: /mcp sin token → {r.status_code}")
            prm = httpx.get(f"{base}/.well-known/oauth-protected-resource/mcp", timeout=20).json()
            check(prm.get("resource", "").rstrip("/") == f"{base}/mcp", f"{c}: protected resource metadata")
        except (httpx.HTTPError, ValueError) as exc:
            check(False, f"{c}: {base} no responde ({exc})")
    # Desde la Mac, MagicDNS resuelve los nombres del tailnet aunque el registro PÚBLICO de Funnel falte
    # (pasó el 30/09 con hub-work: NXDOMAIN afuera, 401 adentro). Preguntamos a un resolver público (DoH).
    print("3) DNS público de Funnel (dns.google)")
    for c in CONTEXTS:
        name = f"hub-{c}.{tailnet}.ts.net"
        try:
            j = httpx.get("https://dns.google/resolve", params={"name": name, "type": "A"}, timeout=10).json()
            n = sum(1 for a in j.get("Answer", []) if a.get("type") == 1)
            check(j.get("Status") == 0 and n > 0,
                  f"{c}: {name} → {n} A (status {j.get('Status')})"
                  + ("" if n else "  ⇒ recreá el sidecar: docker compose up -d --force-recreate ts-" + c + " gateway-" + c))
        except (httpx.HTTPError, ValueError) as exc:
            check(False, f"{c}: no pude consultar DNS público ({exc})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dev", action="store_true")
    mode.add_argument("--quick", action="store_true")
    ap.add_argument("--cognee-url")
    ap.add_argument("--state-dir", default=str(ROOT / "dev" / "state"))
    ap.add_argument("--wait", type=int, default=180)
    args = ap.parse_args()
    if args.dev:
        asyncio.run(dev(args.cognee_url or "http://127.0.0.1:18000", Path(args.state_dir), args.wait))
    else:
        global CONTEXTS
        CONTEXTS = _prod_contexts()
        asyncio.run(quick(args.cognee_url or "http://127.0.0.1:8010", os.environ.get("TS_TAILNET") or _tailnet_from_env()))
    print()
    if failures:
        print(f"FALLARON {len(failures)} chequeos")
        sys.exit(1)
    print("TODO OK")


if __name__ == "__main__":
    main()
