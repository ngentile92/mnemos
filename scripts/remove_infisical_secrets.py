#!/usr/bin/env python3
"""Borra secretos de un proyecto/carpeta de Infisical (p. ej. después de moverlos a otro contexto).

  .venv/bin/python3 scripts/remove_infisical_secrets.py --project hub-personal --path / \\
      --require-in hub-side:/shop --name MISTRAL_API_KEY --name XAI_API_KEY            # plan (no escribe)
  ... --apply                                                                                  # borra de verdad

Sin --apply solo muestra el plan y no pide token. Con --apply:
  * pide el token igual que import_env_secrets.py (INFISICAL_ADMIN_TOKEN, identity de import o getpass);
  * si se pasa --require-in PROYECTO:CARPETA, verifica ANTES que todos los nombres existan ahí y, si falta
    alguno, no borra nada;
  * borra solo los nombres pedidos en --project/--path (entorno --env, default prod).
Nunca imprime valores: de los listados de Infisical solo se usan los nombres (secretKey).
No toca config/secret-policy.yaml: sacá a mano las entradas del contexto de origen y revisá el diff.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_env_secrets import NAME_RE, Infisical, check_url, get_token  # noqa: E402


def list_keys(inf: Infisical, pid: str, env: str, path: str) -> set[str]:
    """Nombres de los secretos de una carpeta (los valores que devuelva la API se descartan)."""
    r = inf.c.get("/api/v4/secrets", params={"projectId": pid, "environment": env, "secretPath": path,
                                             "viewSecretValue": "false"})
    if r.status_code == 404:
        r = inf.c.get("/api/v3/secrets/raw", params={"workspaceId": pid, "environment": env, "secretPath": path})
    if r.status_code != 200:
        raise SystemExit(f"listar {path}: {inf._msg(r)}")
    return {s["secretKey"] for s in r.json().get("secrets", []) if s.get("secretKey")}


def delete_secret(inf: Infisical, pid: str, env: str, path: str, name: str) -> str:
    body = {"projectId": pid, "environment": env, "secretPath": path, "type": "shared"}
    r = inf.c.request("DELETE", f"/api/v4/secrets/{name}", json=body)
    if r.status_code == 404 and "not found" not in inf._msg(r).lower():
        body3 = {**body, "workspaceId": pid}
        body3.pop("projectId")
        r = inf.c.request("DELETE", f"/api/v3/secrets/raw/{name}", json=body3)
    if r.status_code in (200, 201, 204):
        return "borrado"
    return f"ERROR: {inf._msg(r)}"


def parse_loc(s: str) -> tuple[str, str]:
    if ":" not in s:
        raise SystemExit(f"--require-in tiene que ser PROYECTO:CARPETA (p. ej. hub-side:/shop), no {s!r}")
    proj, path = s.split(":", 1)
    return proj, path or "/"


def run(args: argparse.Namespace, inf: Infisical | None = None) -> int:
    names = list(dict.fromkeys(args.name))
    bad = [n for n in names if not NAME_RE.match(n)]
    if bad:
        raise SystemExit(f"nombres inválidos: {', '.join(bad)}")
    req = parse_loc(args.require_in) if args.require_in else None
    print(f"{'APPLY' if args.apply else 'PLAN'} · borrar de {args.project}:{args.path} ({args.env}) · {len(names)} nombres"
          + (f" · exige que existan en {req[0]}:{req[1]}" if req else ""))
    for n in names:
        print(f"  - {n}")
    if not args.apply:
        print("\nNada borrado. Repetí con --apply.")
        return 0
    if inf is None:
        base = args.url.rstrip("/")
        check_url(base)
        inf = Infisical(base, get_token(base))
    pids = inf.project_ids()
    for slug in [args.project] + ([req[0]] if req else []):
        if slug not in pids:
            raise SystemExit(f"proyecto desconocido: {slug} (hay: {', '.join(sorted(pids))})")
    if req:
        dest = list_keys(inf, pids[req[0]], args.env, req[1])
        missing = [n for n in names if n not in dest]
        if missing:
            raise SystemExit(f"no borro nada: faltan en {req[0]}:{req[1]}: {', '.join(missing)}")
        print(f"\n✓ los {len(names)} existen en {req[0]}:{req[1]}")
    src = list_keys(inf, pids[args.project], args.env, args.path)
    errors = 0
    print()
    for n in names:
        if n not in src:
            print(f"  · {n}: no estaba en {args.project}:{args.path}")
            continue
        res = delete_secret(inf, pids[args.project], args.env, args.path, n)
        errors += res.startswith("ERROR")
        print(f"  {'✗' if res.startswith('ERROR') else '✓'} {n}: {res}")
    left = list_keys(inf, pids[args.project], args.env, args.path) & set(names)
    print(f"\nQuedan en {args.project}:{args.path}: {', '.join(sorted(left)) or 'ninguno de los pedidos'}")
    return 1 if errors or left else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="slug del proyecto de origen (p. ej. hub-personal)")
    ap.add_argument("--path", default="/", help="carpeta de origen (default /)")
    ap.add_argument("--env", default="prod")
    ap.add_argument("--name", action="append", required=True, help="nombre a borrar (repetible)")
    ap.add_argument("--require-in", help="PROYECTO:CARPETA donde tienen que existir antes de borrar")
    ap.add_argument("--apply", action="store_true", help="borrar de verdad (sin esto solo muestra el plan)")
    ap.add_argument("--url", default="http://127.0.0.1:8082")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
