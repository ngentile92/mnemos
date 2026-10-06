#!/usr/bin/env python3
"""Prueba end-to-end de la capa de secretos contra un Infisical REAL y NUEVO (descartable).

  docker compose --env-file <env de prueba> -p mnemos-inftest up -d infisical-db infisical-redis infisical
  python dev/infisical_e2e.py --url http://127.0.0.1:8082

Hace: bootstrap de la instancia, proyectos/carpetas/identidades (scripts/bootstrap_infisical.py),
carga secretos FALSOS, y verifica con el código del gateway que:
  * cada identidad lee su proyecto y hub-shared, y NO los de otro contexto;
  * secret_http_request inyecta el valor y lo redacta aunque el servicio lo devuelva (httpbin.org/anything).
NUNCA lo corras contra tu Infisical real.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateway" / "src"))
import bootstrap_infisical as bi  # noqa: E402
from hub_gateway.secrets import InfisicalFetcher, SecretBroker, parse_section  # noqa: E402

FAKE = {"hub-personal": ("PERSONAL_TOKEN", "/", "fake-personal-Zq81"),
        "hub-work": ("WORK_TOKEN", "/", "fake-work-Xy77"),
        "hub-side": ("BLOG_KEY", "/blog", "fake-blog-Ab12"),
        "hub-shared": ("SHARED_TOKEN", "/", "fake-shared-Cd34")}
fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


async def main(base: str) -> None:
    r = httpx.post(f"{base}/api/v1/admin/bootstrap", timeout=60,
                   json={"email": "e2e@example.com", "password": "e2e-password-not-real-123!", "organization": "e2e"})
    if r.status_code != 200:
        raise SystemExit(f"bootstrap falló ({r.status_code}); ¿instancia no nueva? {r.text[:200]}")
    data = r.json()
    api = bi.Api(base, data["identity"]["credentials"]["token"])
    ids = {s: bi.ensure_project(api, s) for s in [*bi.CONTEXT_PROJECTS.values(), bi.SHARED_PROJECT]}
    for f in bi.SIDE_FOLDERS:
        bi.ensure_folder(api, ids["hub-side"], f)
    creds = {}
    for ctx, slug in bi.CONTEXT_PROJECTS.items():
        creds[ctx] = bi.create_identity(api, data["organization"]["id"], ctx, [ids[slug], ids[bi.SHARED_PROJECT]])
    for slug, (name, path, value) in FAKE.items():
        api.req("POST", f"/api/v4/secrets/{name}",
                json={"projectId": ids[slug], "environment": "prod", "secretPath": path, "secretValue": value})
    print("instancia preparada: 4 proyectos, 3 identidades, 4 secretos falsos")

    def spec(slug, host="httpbin.org"):
        name, path, _ = FAKE[slug]
        return parse_section({name: {"project": slug, "path": path, "allowed_hosts": [host],
                                     "inject": {"header": "Authorization", "format": "Bearer {value}"}}})[name]

    print("1) aislamiento por identidad")
    for ctx, (cid, sec) in creds.items():
        f = InfisicalFetcher(base, cid, sec, "prod")
        own = bi.CONTEXT_PROJECTS[ctx]
        check(await f.fetch(spec(own)) == FAKE[own][2], f"mi-gw-{ctx} lee {own}")
        check(await f.fetch(spec("hub-shared")) == FAKE["hub-shared"][2], f"mi-gw-{ctx} lee hub-shared")
        for other in bi.CONTEXT_PROJECTS.values():
            if other == own:
                continue
            try:
                await f.fetch(spec(other))
                check(False, f"mi-gw-{ctx} NO debería leer {other}")
            except Exception:
                check(True, f"mi-gw-{ctx} no puede leer {other}")

    print("2) inyección + redacción con un servicio que devuelve los headers (httpbin.org/anything)")
    cid, sec = creds["personal"]
    broker = SecretBroker({"PERSONAL_TOKEN": spec("hub-personal")}, InfisicalFetcher(base, cid, sec, "prod"))
    try:
        res = await broker.request("PERSONAL_TOKEN", "GET", "https://httpbin.org/anything")
        check(res["status"] == 200, f"request a httpbin.org → {res['status']}")
        check(FAKE["hub-personal"][2] not in repr(res), "el valor NO aparece en la respuesta")
        check("[REDACTED:PERSONAL_TOKEN]" in res["body"], "el eco del header aparece redactado")
    except Exception as exc:  # httpbin puede estar caído: no es un fallo del hub
        print(f"  SKIP httpbin.org no disponible ({type(exc).__name__}: {exc})")
    try:
        await broker.request("PERSONAL_TOKEN", "GET", "https://example.com/")
        check(False, "example.com debería rechazarse")
    except Exception as exc:
        check("no permitido" in str(exc), "host fuera de la allowlist → rechazado sin pedir el secreto")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8082")
    asyncio.run(main(ap.parse_args().url.rstrip("/")))
    print("\nFALLARON %d" % len(fails) if fails else "\nTODO OK")
    sys.exit(1 if fails else 0)
