#!/usr/bin/env python3
"""Reprocesa al grafo las memorias que quedaron pendientes en Cognee (no borra nada).

Para cada contexto usa SU API key (COGNEE_KEY_<CTX>, la del gateway) sobre sus datasets y `shared`:
mira /datasets/{id}/processing-status y, si hay ítems sin procesar, lanza /api/v1/cognify para ese dataset.
Cognee procesa en modo incremental: los ítems ya completados se saltean y los nodos se deduplican por id.

Uso:
  python3 scripts/cognify_pending.py              # lanza cognify donde haga falta y espera el resultado
  python3 scripts/cognify_pending.py --dry-run    # solo muestra qué está pendiente
  python3 scripts/cognify_pending.py --no-wait    # lanza y sale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

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
from bootstrap_cognee import read_env  # noqa: E402
from hub_gateway.contexts import CONTEXTS, SHARED  # noqa: E402

BASE = "http://127.0.0.1:8010"
DONE = {"DATASET_PROCESSING_COMPLETED", "DATASET_PROCESSING_ERRORED"}


def plan() -> list[tuple[str, str]]:
    """(contexto cuya key se usa, dataset). shared se procesa con la key del primer contexto (todos tienen write)."""
    out = [(c, d) for c, spec in CONTEXTS.items() for d in spec.own_dataset_names]
    return [*out, (next(iter(CONTEXTS)), SHARED)]


def check_is_cognee(c: httpx.Client) -> None:
    r = c.get("/health")
    if r.status_code != 200 or r.headers.get("server") != "uvicorn" or "version" not in r.json():
        raise SystemExit(f"{BASE} no responde como Cognee; no mando credenciales")


def graph_size(c: httpx.Client, did: str, hdr: dict) -> tuple[int, int]:
    g = c.get(f"/api/v1/datasets/{did}/graph", headers=hdr)
    if g.status_code != 200:
        return (-1, -1)
    j = g.json()
    return len(j.get("nodes", [])), len(j.get("edges", []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600, help="segundos máximos de espera (default 3600)")
    args = ap.parse_args(argv)
    env = read_env(ROOT / ".env")
    ids = json.loads((ROOT / "config" / "cognee-datasets.json").read_text())["datasets"]
    c = httpx.Client(base_url=BASE, timeout=120)
    check_is_cognee(c)
    launched: dict[str, tuple[str, dict]] = {}
    for ctx, name in plan():
        key = env.get(f"COGNEE_KEY_{ctx.upper()}")
        if not key or name not in ids:
            print(f"{name}: sin key o sin id, lo salteo")
            continue
        hdr = {"X-Api-Key": key}
        ps = c.get(f"/api/v1/datasets/{ids[name]}/processing-status", headers=hdr)
        ps.raise_for_status()
        p = ps.json()
        n, e = graph_size(c, ids[name], hdr)
        print(f"{name:18} ítems={p.get('total')} completos={p.get('completed')} pendientes={p.get('pending')} "
              f"grafo={n} nodos/{e} aristas")
        if not p.get("pending") or args.dry_run:
            continue
        r = c.post("/api/v1/cognify", json={"dataset_ids": [ids[name]], "run_in_background": True}, headers=hdr)
        if r.status_code >= 400:
            print(f"  cognify {name}: HTTP {r.status_code}")
            continue
        launched[name] = (ids[name], hdr)
        print(f"  → cognify lanzado para {name}")
    if not launched or args.no_wait or args.dry_run:
        return 0
    t0 = time.time()
    pending = dict(launched)
    started: set[str] = set()
    while pending and time.time() - t0 < args.timeout:
        time.sleep(20)
        for name, (did, hdr) in list(pending.items()):
            st = c.get("/api/v1/datasets/status", params={"dataset": did}, headers=hdr)
            status = st.json().get(did) if st.status_code == 200 else None
            if status == "DATASET_PROCESSING_STARTED":
                started.add(name)
            # no confundir el estado del run anterior con el nuevo: esperar a verlo arrancar (o 2 min)
            if status in DONE and (name in started or time.time() - t0 > 120):
                p = c.get(f"/api/v1/datasets/{did}/processing-status", headers=hdr).json()
                n, e = graph_size(c, did, hdr)
                print(f"{name:18} {status.removeprefix('DATASET_PROCESSING_')} completos={p.get('completed')}/"
                      f"{p.get('total')} grafo={n} nodos/{e} aristas  ({round(time.time() - t0)} s)")
                pending.pop(name)
    for name in pending:
        print(f"{name}: sigue procesando después de {args.timeout} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
