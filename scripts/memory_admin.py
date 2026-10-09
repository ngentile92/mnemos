#!/usr/bin/env python3
"""Listar / borrar notas de la memoria como hub-admin (única vía para corregir `shared`).

Los conectores (gateways) solo pueden borrar/corregir notas de los datasets PROPIOS de su contexto
(memory_delete / memory_update / memory_undo); `shared` es de hub-admin y los ctx-* solo tienen read+write ahí,
así que esas correcciones pasan por acá, en la Mac, contra Cognee en 127.0.0.1:8010.

  .venv/bin/python3 scripts/memory_admin.py list shared [--contains texto]
  .venv/bin/python3 scripts/memory_admin.py delete shared <data_id> [--yes]

Sin --yes, delete muestra la nota y no borra nada. Usa COGNEE_PW_ADMIN de .env (nunca lo imprime).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8010"
ADMIN_EMAIL = "hub-admin@example.com"
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)(\s+#.*)?$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def admin_client(base: str, env: dict[str, str]) -> httpx.Client:
    h = httpx.get(f"{base}/health", timeout=10)
    if h.status_code != 200 or "version" not in h.text:  # no mandar la contraseña a otra cosa en el puerto
        raise SystemExit(f"{base} no responde como Cognee")
    pw = env.get("COGNEE_PW_ADMIN")
    if not pw:
        raise SystemExit("falta COGNEE_PW_ADMIN en .env")
    c = httpx.Client(base_url=base, timeout=60, trust_env=False)
    r = c.post("/api/v1/auth/login", data={"username": ADMIN_EMAIL, "password": pw})
    if r.status_code != 200:
        raise SystemExit(f"login hub-admin: HTTP {r.status_code}")
    token = (r.json() or {}).get("access_token") if r.headers.get("content-type", "").startswith("application/json") else None
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "delete"])
    ap.add_argument("dataset", help="nombre del dataset (p. ej. shared, work, side_blog)")
    ap.add_argument("data_id", nargs="?")
    ap.add_argument("--contains")
    ap.add_argument("--yes", action="store_true", help="borrar de verdad (sin esto, delete solo muestra)")
    ap.add_argument("--url", default=BASE)
    args = ap.parse_args()

    ids = json.loads((ROOT / "config" / "cognee-datasets.json").read_text())["datasets"]
    if args.dataset not in ids:
        raise SystemExit(f"dataset desconocido: {args.dataset} (opciones: {', '.join(sorted(ids))})")
    ds = ids[args.dataset]
    c = admin_client(args.url.rstrip("/"), read_env(ROOT / ".env"))
    r = c.get(f"/api/v1/datasets/{ds}/data")
    r.raise_for_status()
    items = r.json()

    def text_of(did: str) -> str:
        t = c.get(f"/api/v1/datasets/{ds}/data/{did}/raw")
        return t.content.decode("utf-8", "replace") if t.status_code == 200 else f"(HTTP {t.status_code})"

    if args.action == "list":
        for d in sorted(items, key=lambda d: str(d.get("createdAt")), reverse=True):
            t = text_of(d["id"])
            if args.contains and args.contains.lower() not in t.lower():
                continue
            print(f"{d['id']}  {str(d.get('createdAt'))[:19]}  {t[:160].replace(chr(10), ' ')}")
        return
    if not args.data_id or not UUID_RE.match(args.data_id):
        raise SystemExit("delete necesita el data_id (UUID) que muestra `list`")
    if not any(str(d.get("id")) == args.data_id for d in items):
        raise SystemExit(f"{args.data_id} no está en {args.dataset}")
    print(f"[{args.dataset}] {args.data_id}\n{text_of(args.data_id)[:2000]}\n")
    if not args.yes:
        print("(sin --yes: no borré nada)")
        return
    r = c.delete(f"/api/v1/datasets/{ds}/data/{args.data_id}")
    if r.status_code >= 300:
        raise SystemExit(f"Cognee no borró: HTTP {r.status_code}")
    print("borrada")


if __name__ == "__main__":
    sys.exit(main())
