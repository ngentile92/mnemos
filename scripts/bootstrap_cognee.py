#!/usr/bin/env python3
"""Bootstrap idempotente de Cognee para el hub.

Crea (o reutiliza) los usuarios hub-admin / ctx-work / ctx-personal / ctx-side, los datasets
de cada contexto y `shared`, asigna permisos read+write sobre `shared` a los usuarios de contexto,
crea una API key por usuario de contexto y escribe:
  * config/cognee-datasets.json   (nombre -> UUID; no es secreto, se puede versionar)
  * las API keys en .env          (COGNEE_KEY_WORK / _PERSONAL / _SIDE)

Contraseñas: se leen de .env (COGNEE_PW_ADMIN, COGNEE_PW_WORK, ...). bootstrap.sh las genera.
Solo usa la librería estándar + httpx.

Uso:
  python scripts/bootstrap_cognee.py                       # producción (Cognee en 127.0.0.1:8010)
  python scripts/bootstrap_cognee.py --url http://127.0.0.1:18000 --dev \
      --datasets-out dev/state/cognee-datasets.json --dev-keys-dir dev/state
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.contexts import CONTEXTS, DATASET_OWNER, SHARED  # noqa: E402

EMAIL_DOMAIN = "example.com"  # cuentas técnicas internas; Cognee exige formato email
USERS = ["hub-admin"] + [f"ctx-{c}" for c in CONTEXTS]


def pw_var(user: str) -> str:
    return "COGNEE_PW_ADMIN" if user == "hub-admin" else f"COGNEE_PW_{user[4:].upper()}"


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)(\s+#.*)?$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def upsert_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in values:
            lines[i] = f"{m.group(1)}={values[m.group(1)]}"
            seen.add(m.group(1))
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


class Session:
    def __init__(self, base: str, email: str, password: str) -> None:
        self.base = base
        self.email = email
        self.password = password
        self.c = httpx.Client(base_url=base, timeout=60)
        self.id: str | None = None

    def register_or_login(self) -> None:
        r = self.c.post("/api/v1/auth/register", json={"email": self.email, "password": self.password})
        if r.status_code not in (200, 201, 400):
            raise SystemExit(f"register {self.email}: HTTP {r.status_code} {r.text[:200]}")
        r = self.c.post("/api/v1/auth/login", data={"username": self.email, "password": self.password})
        if r.status_code not in (200, 204):
            raise SystemExit(f"login {self.email}: HTTP {r.status_code} {r.text[:200]} "
                             "(¿cambiaste la contraseña en .env después del primer bootstrap?)")
        try:
            token = r.json().get("access_token")
        except ValueError:
            token = None
        if token:
            self.c.headers["Authorization"] = f"Bearer {token}"
        me = self.c.get("/api/v1/users/me")  # fastapi-users: devuelve id
        me.raise_for_status()
        self.id = str(me.json()["id"])

    def dataset(self, name: str) -> str:
        r = self.c.post("/api/v1/datasets", json={"name": name})
        r.raise_for_status()
        return str(r.json()["id"])

    def grant(self, principal_id: str, permission: str, dataset_ids: list[str]) -> None:
        r = self.c.post(f"/api/v1/permissions/datasets/{principal_id}",
                        params={"permission_name": permission}, json=dataset_ids)
        if r.status_code != 200:
            raise SystemExit(f"grant {permission} a {principal_id}: HTTP {r.status_code} {r.text[:200]}")

    def create_api_key(self) -> str:
        r = self.c.post("/api/v1/auth/api-keys", json={"name": "hub-gateway"})
        r.raise_for_status()
        return r.json()["key"]


def key_is_valid(base: str, key: str | None) -> bool:
    if not key or key.startswith("__"):
        return False
    r = httpx.get(f"{base}/api/v1/auth/me", headers={"X-Api-Key": key}, timeout=15)
    return r.status_code == 200


def wait_ready(base: str, timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise SystemExit(f"Cognee no respondió en {base} tras {timeout}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--datasets-out", default=str(ROOT / "config" / "cognee-datasets.json"))
    ap.add_argument("--dev", action="store_true", help="contraseñas fijas de desarrollo (NUNCA en producción)")
    ap.add_argument("--dev-keys-dir", help="en dev: escribe <ctx>.env con COGNEE_API_KEY en este directorio")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    env_path = Path(args.env_file)
    env = read_env(env_path)
    wait_ready(base)

    sessions: dict[str, Session] = {}
    for user in USERS:
        pw = "dev-password-123!" if args.dev else env.get(pw_var(user))
        if not pw or pw.startswith("__"):
            raise SystemExit(f"Falta {pw_var(user)} en {env_path} (corré scripts/bootstrap.sh primero)")
        s = Session(base, f"{user}@{EMAIL_DOMAIN}", pw)
        s.register_or_login()
        sessions[user] = s
        print(f"usuario {user}: {s.id}")

    ids: dict[str, str] = {}
    for ds, owner in DATASET_OWNER.items():
        ids[ds] = sessions[owner].dataset(ds)
        print(f"dataset {ds} (dueño {owner}): {ids[ds]}")

    admin = sessions["hub-admin"]
    for c in CONTEXTS:
        for perm in ("read", "write"):
            admin.grant(sessions[f"ctx-{c}"].id, perm, [ids[SHARED]])
    print("permisos: ctx-* tienen read+write sobre shared (sin delete ni share)")
    for br in BRIDGES:  # opt-in en contexts.yaml: el lector solo recibe read
        admin_or_owner = sessions[f"ctx-{br.source}"]
        admin_or_owner.grant(sessions[f"ctx-{br.reader}"].id, "read", [ids[d] for d in br.datasets])
        print(f"bridge: ctx-{br.reader} puede leer {', '.join(br.datasets)} (solo read)")

    out = Path(args.datasets_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"datasets": ids, "users": {u: s.id for u, s in sessions.items()}}, indent=2) + "\n")
    print(f"escrito {out}")

    new_keys: dict[str, str] = {}
    for c in CONTEXTS:
        var = f"COGNEE_KEY_{c.upper()}"
        existing = env.get(var)
        if args.dev_keys_dir:
            p = Path(args.dev_keys_dir) / f"{c}.env"
            existing = read_env(p).get("COGNEE_API_KEY")
        if key_is_valid(base, existing):
            print(f"{var}: la key existente sigue siendo válida")
            continue
        key = sessions[f"ctx-{c}"].create_api_key()
        if args.dev_keys_dir:
            p = Path(args.dev_keys_dir) / f"{c}.env"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"COGNEE_API_KEY={key}\n")
            os.chmod(p, 0o600)
        else:
            new_keys[var] = key
        print(f"{var}: key nueva creada")
    if new_keys:
        upsert_env(env_path, new_keys)
        print(f"keys guardadas en {env_path}")
    print("OK. Reiniciá los gateways: docker compose up -d --force-recreate gateway-work gateway-personal gateway-side")


if __name__ == "__main__":
    main()
