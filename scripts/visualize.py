#!/usr/bin/env python3
"""Vista global del grafo de Cognee — solo para vos (hub-admin), solo local.

Se autentica como `hub-admin` (contraseña COGNEE_PW_ADMIN de .env), llama a
POST /api/v1/visualize/multi con un par (dueño, dataset) por cada dataset y guarda el HTML en
~/mnemos-graphs/<global|contexto>-AAAA-MM-DD.html (chmod 600: tiene el contenido de tu memoria).
Cada contexto sale de otro color (la vista colorea por usuario dueño).

No agrega ninguna ruta: es un cliente HTTP contra Cognee en 127.0.0.1:8010 (el puerto que compose publica
solo en loopback). Los gateways y los usuarios ctx-* no ganan ningún permiso.

Una sola vez (idempotente): `--setup`
  1. cada dueño (ctx-*) le da `read` a hub-admin sobre sus datasets (solo a hub-admin);
  2. marca a hub-admin como superuser de Cognee (/visualize/multi lo exige), vía
     `docker compose exec cognee` — no hay endpoint para eso sin tener ya un superuser.

Uso:
  python3 scripts/visualize.py --setup          # una vez
  python3 scripts/visualize.py                  # todos los datasets → global-<fecha>.html y lo abre
  python3 scripts/visualize.py --context side   # solo side_* (sumá --with-shared para incluir shared)
  python3 scripts/visualize.py --context shared --no-open
  python3 scripts/visualize.py --revoke-superuser   # deshacer el paso 2
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
try:
    import httpx
except ModuleNotFoundError:  # `python3` del sistema sin deps → re-ejecutar con el .venv del repo
    if (VENV / "bin" / "python3").exists() and Path(sys.prefix).resolve() != VENV.resolve():
        os.execv(str(VENV / "bin" / "python3"), [str(VENV / "bin" / "python3"), __file__, *sys.argv[1:]])
    raise SystemExit("falta httpx: creá el venv del repo (python3 -m venv .venv && .venv/bin/pip install -e gateway)")

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from bootstrap_cognee import EMAIL_DOMAIN, pw_var, read_env  # noqa: E402
from hub_gateway.contexts import ALL_DATASET_NAMES, CONTEXTS, DATASET_OWNER, SHARED  # noqa: E402

ADMIN = "hub-admin"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Corre DENTRO del contenedor de Cognee (usa su propio engine; no toca contraseñas ni keys).
PROMOTE_SNIPPET = """
import asyncio, sys
from sqlalchemy import update
from cognee.infrastructure.databases.relational import get_relational_engine
from cognee.modules.users.models import User

async def main(email, value):
    async with get_relational_engine().get_async_session() as s:
        r = await s.execute(update(User).where(User.email == email).values(is_superuser=value))
        await s.commit()
        print(r.rowcount)

asyncio.run(main(sys.argv[1], sys.argv[2] == "1"))
"""


def select_datasets(context: str | None, with_shared: bool = False) -> list[str]:
    """Nombres de dataset a graficar. None = todos."""
    if context is None:
        return list(ALL_DATASET_NAMES)
    if context == SHARED:
        return [SHARED]
    if context not in CONTEXTS:
        raise SystemExit(f"contexto inválido: {context!r}. Opciones: {', '.join([*CONTEXTS, SHARED])}")
    names = CONTEXTS[context].own_dataset_names
    return names + [SHARED] if with_shared else names


def build_pairs(names: list[str], state: dict) -> list[dict[str, str]]:
    """Un par {user_id: dueño, dataset_id} por dataset, con los UUIDs de config/cognee-datasets.json."""
    pairs = []
    for name in names:
        owner = DATASET_OWNER[name]
        try:
            pairs.append({"user_id": state["users"][owner], "dataset_id": state["datasets"][name]})
        except KeyError as exc:
            raise SystemExit(f"falta {exc} en el archivo de datasets (¿corriste bootstrap_cognee.py?)") from exc
    return pairs


def output_path(out_dir: Path, context: str | None, today: dt.date | None = None) -> Path:
    today = today or dt.date.today()
    return out_dir / f"{context or 'global'}-{today:%Y-%m-%d}.html"


def ensure_local(url: str) -> str:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"--url debe ser local (127.0.0.1/localhost), no {host!r}: la vista global es solo admin/local")
    return url.rstrip("/")


def check_is_cognee(base: str) -> None:
    """No mandar contraseñas a lo que sea que escuche en el puerto (p. ej. un runserver en 0.0.0.0:8000
    que atiende mientras Cognee reinicia)."""
    try:
        r = httpx.get(f"{base}/health", timeout=10)
        ok = r.status_code == 200 and r.headers.get("server") == "uvicorn" and "version" in r.json()
    except (httpx.HTTPError, ValueError):
        ok = False
    if not ok:
        raise SystemExit(f"{base} no responde como Cognee (¿está reiniciando? `docker compose ps cognee`); "
                         "no mando credenciales")


def login(base: str, user: str, env: dict[str, str]) -> tuple[httpx.Client, dict]:
    pw = env.get(pw_var(user))
    if not pw or pw.startswith("__"):
        raise SystemExit(f"falta {pw_var(user)} en .env")
    c = httpx.Client(base_url=base, timeout=120)
    r = c.post("/api/v1/auth/login", data={"username": f"{user}@{EMAIL_DOMAIN}", "password": pw})
    if r.status_code not in (200, 204):
        raise SystemExit(f"login {user}: HTTP {r.status_code}")
    try:
        token = r.json().get("access_token")
    except ValueError:
        token = None
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    me = c.get("/api/v1/users/me")
    me.raise_for_status()
    return c, me.json()


def setup(base: str, env: dict[str, str], state: dict) -> None:
    _, me = login(base, ADMIN, env)
    admin_id = str(me["id"])
    by_owner: dict[str, list[str]] = {}
    for name, owner in DATASET_OWNER.items():
        if owner != ADMIN:
            by_owner.setdefault(owner, []).append(state["datasets"][name])
    for owner, ds_ids in by_owner.items():
        c, _ = login(base, owner, env)
        r = c.post(f"/api/v1/permissions/datasets/{admin_id}", params={"permission_name": "read"}, json=ds_ids)
        if r.status_code != 200:
            raise SystemExit(f"{owner} → read a {ADMIN}: HTTP {r.status_code} {r.text[:200]}")
        print(f"read a {ADMIN} sobre los datasets de {owner} ({len(ds_ids)})")

    if not me.get("is_superuser"):
        set_superuser(True)
    _, me = login(base, ADMIN, env)
    if not me.get("is_superuser"):
        raise SystemExit(f"{ADMIN} sigue sin ser superuser")
    print(f"{ADMIN} es superuser. Listo: python3 scripts/visualize.py")


def set_superuser(value: bool) -> None:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "cognee", "python", "-c", PROMOTE_SNIPPET,
         f"{ADMIN}@{EMAIL_DOMAIN}", "1" if value else "0"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0 or out.stdout.strip().splitlines()[-1:] != ["1"]:
        raise SystemExit(f"no pude cambiar is_superuser de {ADMIN}:\n{out.stderr[-800:]}")


def render(base: str, env: dict[str, str], pairs: list[dict[str, str]]) -> str:
    admin, me = login(base, ADMIN, env)
    if not me.get("is_superuser"):
        raise SystemExit(f"{ADMIN} no es superuser de Cognee: corré una vez `python3 scripts/visualize.py --setup`")
    r = admin.post("/api/v1/visualize/multi", json=pairs)
    if r.status_code != 200:
        raise SystemExit(f"/visualize/multi: HTTP {r.status_code} {r.text[:300]}")
    return r.text


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        webbrowser.open(path.as_uri())


def main(argv: list[str] | None = None) -> Path | None:
    import json

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--datasets-file", default=str(ROOT / "config" / "cognee-datasets.json"))
    ap.add_argument("--context", choices=[*CONTEXTS, SHARED], help="un solo contexto (default: todos)")
    ap.add_argument("--with-shared", action="store_true", help="con --context: sumar el dataset shared")
    ap.add_argument("--out-dir", default=str(Path.home() / "mnemos-graphs"))
    ap.add_argument("--no-open", action="store_true", help="no abrir el HTML")
    ap.add_argument("--setup", action="store_true", help="una vez: read + superuser para hub-admin")
    ap.add_argument("--revoke-superuser", action="store_true", help="deshace el superuser de hub-admin")
    args = ap.parse_args(argv)

    base = ensure_local(args.url)
    check_is_cognee(base)
    env = read_env(Path(args.env_file))
    state = json.loads(Path(args.datasets_file).read_text())

    if args.revoke_superuser:
        set_superuser(False)
        print(f"{ADMIN} ya no es superuser (conserva read sobre los datasets)")
        return None
    if args.setup:
        setup(base, env, state)
        return None

    names = select_datasets(args.context, args.with_shared)
    html = render(base, env, build_pairs(names, state))
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    path = output_path(out_dir, args.context)
    path.write_text(html, encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"{path} ({len(html) // 1024} KB, datasets: {', '.join(names)})")
    if not args.no_open:
        open_file(path)
    return path


if __name__ == "__main__":
    main()
