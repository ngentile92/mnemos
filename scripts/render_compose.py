#!/usr/bin/env python3
"""Genera el compose de TU instancia a partir de compose.yaml + config/contexts.yaml.

compose.yaml trae tres contextos (work / personal / side). Si tus contextos son otros, este script toma el
bloque `ts-work` + `gateway-work` (y sus volúmenes ts_work / gw_work) como plantilla, lo replica por cada
contexto de contexts.yaml (work → <ctx>, WORK → <CTX> en nombres de servicio, hostnames, volúmenes y
variables de .env) y escribe compose.generated.yaml (gitignored). Después, en .env:

    COMPOSE_FILE=compose.generated.yaml

y `docker compose ...` lo usa solo. scripts/update.sh lo regenera en cada actualización.

    python scripts/render_compose.py            # escribe compose.generated.yaml
    python scripts/render_compose.py --check    # exit 1 si el generado está desactualizado
    python scripts/render_compose.py --stdout

Con los contextos por defecto el resultado es equivalente a compose.yaml (lo verifica un test).
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway" / "src"))

TEMPLATE_CTX = "work"
DEFAULT_CTXS = ("work", "personal", "side")
PER_CTX_SERVICES = ("ts-{}", "gateway-{}")
PER_CTX_VOLUMES = ("ts_{}", "gw_{}")
HEADER = ("# GENERADO por scripts/render_compose.py desde compose.yaml + config/contexts.yaml: NO EDITAR.\n"
          "# Regenerar: python scripts/render_compose.py  (scripts/update.sh lo hace solo)\n")

_LOWER = re.compile(r"(?<![A-Za-z])work(?![a-z])")
_UPPER = re.compile(r"(?<![A-Za-z])WORK(?![A-Z])")


def _sub(value, ctx: str):
    if isinstance(value, str):
        value = _LOWER.sub(ctx, value)
        return _UPPER.sub(ctx.upper().replace("-", "_"), value)
    if isinstance(value, dict):
        return {_sub(k, ctx): _sub(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_sub(v, ctx) for v in value]
    return value


def render(base: dict, contexts: list[str]) -> dict:
    out = {k: copy.deepcopy(v) for k, v in base.items() if not k.startswith("x-")}
    services, volumes = out["services"], out["volumes"]
    templates = {t: services[t.format(TEMPLATE_CTX)] for t in PER_CTX_SERVICES}
    vol_templates = {t: volumes[t.format(TEMPLATE_CTX)] for t in PER_CTX_VOLUMES}
    for c in DEFAULT_CTXS:
        for t in PER_CTX_SERVICES:
            services.pop(t.format(c), None)
        for t in PER_CTX_VOLUMES:
            volumes.pop(t.format(c), None)
    for c in contexts:
        for t, spec in templates.items():
            services[t.format(c)] = _sub(copy.deepcopy(spec), c)
        for t, spec in vol_templates.items():
            volumes[t.format(c)] = copy.deepcopy(spec)
    router = services.get("hub-router")
    if router:  # one HUB_INTERNAL_KEY_<CTX> per context, in context order
        env = {k: v for k, v in router["environment"].items() if not k.startswith("HUB_INTERNAL_KEY_")}
        for c in contexts:
            s = c.upper().replace("-", "_")
            env[f"HUB_INTERNAL_KEY_{s}"] = f"${{HUB_INTERNAL_KEY_{s}:-}}"
        router["environment"] = env
    return out


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):  # noqa: ARG002 - firma de PyYAML
        return True


def dump(data: dict) -> str:
    return HEADER + yaml.dump(data, Dumper=_NoAliases, sort_keys=False, allow_unicode=True, width=200)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=str(ROOT / "compose.yaml"))
    ap.add_argument("--out", default=str(ROOT / "compose.generated.yaml"))
    ap.add_argument("--contexts", help="contexts.yaml (default: el que usa el gateway)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    from hub_gateway.contexts import load_config, parse_contexts

    _, ctxs = parse_contexts(load_config(args.contexts) if args.contexts else load_config())
    text = dump(render(yaml.safe_load(Path(args.base).read_text(encoding="utf-8")), list(ctxs)))
    out = Path(args.out)
    if args.stdout:
        sys.stdout.write(text)
    elif args.check:
        if not out.is_file() or out.read_text(encoding="utf-8") != text:
            print(f"{out.name} desactualizado: corré scripts/render_compose.py", file=sys.stderr)
            return 1
        print(f"{out.name} al día ({', '.join(ctxs)})")
    else:
        out.write_text(text, encoding="utf-8")
        print(f"escribí {out.name} con contextos: {', '.join(ctxs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
