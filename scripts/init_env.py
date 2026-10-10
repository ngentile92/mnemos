#!/usr/bin/env python3
"""Crea/completa .env y cognee.env a partir de los .example sin pisar valores existentes.

- __GENERAR__  → se genera un valor aleatorio adecuado.
- __NOTEBOOK_TS_NAME__ / __TS_TAILNET__ dentro de URLs → se reemplazan si ya definiste esas variables.
- __COMPLETAR__ → queda así y se lista al final (lo cargás vos, ver docs/SETUP.es.md).
Solo librería estándar (corre con el python3 de macOS).
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r"^(?P<k>[A-Z0-9_]+)=(?P<v>[^#\n]*?)(?P<c>\s+#.*)?$")


def gen(name: str) -> str:
    if name == "INFISICAL_ENCRYPTION_KEY":
        return secrets.token_hex(16)
    if name == "INFISICAL_AUTH_SECRET":
        return base64.b64encode(secrets.token_bytes(32)).decode()
    if name.startswith("HUB_STORAGE_KEY_") or name == "MNEMOS_ROUTER_STORAGE_KEY":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()  # clave Fernet
    if name.startswith("HUB_INTERNAL_KEY_"):
        return secrets.token_urlsafe(32)
    if name.startswith("HUB_JWT_SIGNING_KEY_"):
        return secrets.token_hex(32)
    return secrets.token_urlsafe(24)


def parse(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = LINE.match(line.strip())
            if m:
                out[m["k"]] = m["v"].strip()
    return out


def render(example: Path, target: Path) -> list[str]:
    current = parse(target)
    lines_out, pending = [], []
    values: dict[str, str] = {}
    for line in example.read_text().splitlines():
        m = LINE.match(line)
        if not m:
            lines_out.append(line)
            continue
        k, v, c = m["k"], m["v"].strip(), m["c"] or ""
        cur = current.get(k)
        if cur not in (None, "") and "__" not in cur:
            v = cur
        elif v == "__GENERAR__":
            v = gen(k)
        values[k] = v
        lines_out.append(f"{k}={v}{c}")
    # segunda pasada: URLs que dependen de otras variables
    ts, nb = values.get("TS_TAILNET", ""), values.get("NOTEBOOK_TS_NAME", "")
    final = []
    for line in lines_out:
        if "__TS_TAILNET__" in line and ts and "__" not in ts:
            line = line.replace("__TS_TAILNET__", ts)
        if "__NOTEBOOK_TS_NAME__" in line and nb and "__" not in nb:
            line = line.replace("__NOTEBOOK_TS_NAME__", nb)
        m = LINE.match(line)
        if m and "__" in m["v"]:
            pending.append(m["k"])
        final.append(line)
    # variables que existían en el .env pero no en el example (p. ej. COGNEE_KEY_* ya escritas): se conservan
    extra = [f"{k}={v}" for k, v in current.items() if k not in values]
    if extra:
        final += ["", "# --- conservadas del .env anterior ---", *extra]
    target.write_text("\n".join(final) + "\n")
    os.chmod(target, 0o600)
    return pending


def main() -> int:
    missing = []
    for ex, tgt in ((ROOT / ".env.example", ROOT / ".env"), (ROOT / "cognee.env.example", ROOT / "cognee.env")):
        pend = render(ex, tgt)
        print(f"{tgt.name}: listo (chmod 600)")
        missing += [f"{tgt.name}: {k}" for k in pend]
    if missing:
        print("\nFaltan completar a mano (docs/SETUP.es.md):")
        for m in missing:
            print("  -", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
