#!/usr/bin/env python3
"""Bootstrap idempotente de Infisical (self-hosted) para el hub, vía API.

Crea, según config/contexts.yaml: un proyecto hub-<ctx> por contexto + hub-shared (entornos por defecto
dev/staging/prod), una carpeta por proyecto en los contextos con `projects` (con el ejemplo: /shop /blog /mnemos
en hub-side, prod), y una machine identity por contexto
(mi-gw-<ctx>, Universal Auth) con rol Viewer en su proyecto y en hub-shared. Escribe
INF_MI_<CTX>_ID / INF_MI_<CTX>_SECRET en .env.

Necesita un token con permisos de admin de la organización:
  * instancia NUEVA: --bootstrap crea tu cuenta admin + la organización + una "Instance Admin Identity"
    (POST /api/v1/admin/bootstrap) y usa ese token en memoria (no se guarda). Te pide email y contraseña.
  * instancia existente: --token <token de una identity admin> --org-id <id de la organización>.

Ojo con el límite de identidades del plan Free (5): vos + Instance Admin Identity + 3 gateways = 5.
Si no la necesitás más, borrá la "Instance Admin Identity" desde la UI al terminar.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from bootstrap_cognee import read_env, upsert_env  # noqa: E402

sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.contexts import CONTEXTS  # noqa: E402

CONTEXT_PROJECTS = {c: f"hub-{c}" for c in CONTEXTS}
SHARED_PROJECT = "hub-shared"
PROJECT_FOLDERS = {f"hub-{c}": spec.projects for c, spec in CONTEXTS.items() if spec.projects}
ENV = "prod"


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def req(self, method: str, path: str, ok=(200, 201), **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code not in ok:
            raise SystemExit(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}


def ensure_project(api: Api, slug: str) -> str:
    projects = api.req("GET", "/api/v1/projects", params={"type": "secret-manager"}).get("projects", [])
    for p in projects:
        if p.get("slug") == slug:
            return p["id"]
    p = api.req("POST", "/api/v1/projects", json={"projectName": slug, "slug": slug,
                                                  "projectDescription": "mnemos", "type": "secret-manager"})
    return p["project"]["id"]


def ensure_folder(api: Api, project_id: str, name: str) -> None:
    r = api.c.post("/api/v1/folders", json={"workspaceId": project_id, "environment": ENV, "name": name, "path": "/"})
    if r.status_code not in (200, 201, 400, 409):
        raise SystemExit(f"folder {name}: HTTP {r.status_code} {r.text[:200]}")


def login_ok(base: str, client_id: str | None, secret: str | None) -> bool:
    if not client_id or not secret:
        return False
    r = httpx.post(f"{base}/api/v1/auth/universal-auth/login",
                   json={"clientId": client_id, "clientSecret": secret}, timeout=30)
    return r.status_code == 200


def create_identity(api: Api, org_id: str, ctx: str, project_ids: list[str]) -> tuple[str, str]:
    ident = api.req("POST", "/api/v1/identities", json={"name": f"mi-gw-{ctx}", "organizationId": org_id,
                                                        "role": "no-access"})["identity"]
    iid = ident["id"]
    ua = api.req("POST", f"/api/v1/auth/universal-auth/identities/{iid}",
                 json={"accessTokenTTL": 3600, "accessTokenMaxTTL": 86400})["identityUniversalAuth"]
    secret = api.req("POST", f"/api/v1/auth/universal-auth/identities/{iid}/client-secrets",
                     json={"description": f"gateway-{ctx}"})["clientSecret"]
    for pid in project_ids:
        api.req("POST", f"/api/v1/projects/{pid}/memberships/identities/{iid}",
                json={"roles": [{"role": "viewer", "isTemporary": False}]})
    return ua["clientId"], secret


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8082")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--bootstrap", action="store_true", help="instancia nueva: crea admin + organización")
    ap.add_argument("--email", default=os.environ.get("INFISICAL_ADMIN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("INFISICAL_ADMIN_PASSWORD"),
                    help="mejor no pasarla por argumento: si falta se pide por teclado")
    ap.add_argument("--organization", default="mnemos")
    ap.add_argument("--token", default=os.environ.get("INFISICAL_ADMIN_TOKEN"))
    ap.add_argument("--org-id", default=os.environ.get("INFISICAL_ORG_ID"))
    args = ap.parse_args()
    base = args.url.rstrip("/")
    env_path = Path(args.env_file)
    env = read_env(env_path)

    if args.bootstrap:
        email = args.email or input("Email de tu cuenta admin de Infisical: ").strip()
        password = args.password or getpass.getpass("Contraseña (mínimo 14 caracteres recomendado): ")
        r = httpx.post(f"{base}/api/v1/admin/bootstrap", timeout=60,
                       json={"email": email, "password": password, "organization": args.organization})
        if r.status_code != 200:
            raise SystemExit(f"bootstrap: HTTP {r.status_code} {r.text[:300]} (¿la instancia ya estaba inicializada? "
                             "usá --token y --org-id)")
        data = r.json()
        token, org_id = data["identity"]["credentials"]["token"], data["organization"]["id"]
        print(f"instancia inicializada: admin {email}, organización {data['organization']['slug']}")
    else:
        if not args.token or not args.org_id:
            raise SystemExit("Pasá --bootstrap (instancia nueva) o --token y --org-id")
        token, org_id = args.token, args.org_id

    api = Api(base, token)
    ids = {slug: ensure_project(api, slug) for slug in [*CONTEXT_PROJECTS.values(), SHARED_PROJECT]}
    for slug, pid in ids.items():
        print(f"proyecto {slug}: {pid}")
    for slug, folders in PROJECT_FOLDERS.items():
        for f in folders:
            ensure_folder(api, ids[slug], f)
        print(f"carpetas en {slug}/{ENV}: {', '.join('/' + f for f in folders)}")

    new_vals: dict[str, str] = {}
    for ctx, slug in CONTEXT_PROJECTS.items():
        cid_var, sec_var = f"INF_MI_{ctx.upper()}_ID", f"INF_MI_{ctx.upper()}_SECRET"
        if login_ok(base, env.get(cid_var), env.get(sec_var)):
            print(f"mi-gw-{ctx}: credenciales existentes válidas")
            continue
        cid, sec = create_identity(api, org_id, ctx, [ids[slug], ids[SHARED_PROJECT]])
        new_vals[cid_var], new_vals[sec_var] = cid, sec
        print(f"mi-gw-{ctx}: creada (Viewer en {slug} y {SHARED_PROJECT})")
    if new_vals:
        upsert_env(env_path, new_vals)
        print(f"credenciales guardadas en {env_path}")
    print("OK. Cargá los secretos desde la UI y listalos en config/secret-policy.yaml (sin valores).")


if __name__ == "__main__":
    main()
