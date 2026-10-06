#!/usr/bin/env python3
"""Importa secretos a Infisical leyendo los valores DIRECTO de los .env de origen (en la Mac).

Flujo:
  1. Revisás la selección (YAML, sin valores): qué variable de qué archivo va a qué contexto/carpeta/nombre.
  2. `--dry-run` (default): valida todo localmente y muestra el plan. No toca Infisical ni la policy.
  3. `--apply`: crea los secretos en Infisical (prod) y agrega los NOMBRES a config/secret-policy.yaml.

Garantías:
  * Nunca imprime valores ni hashes. Los valores solo viajan en el body del request a Infisical.
  * Los hosts derivados con `allowed_hosts_from` (p. ej. el host de SUPABASE_URL) no se imprimen salvo
    `--show-derived-hosts`; sí se escriben en la policy (que vos revisás con `git diff` antes de commitear).
  * `expect_same`: verifica en memoria (compare_digest) que otras ocurrencias tengan el MISMO valor; si no, no importa.
  * No pisa secretos existentes salvo `--overwrite`.

Auth (necesita escritura: las identities mi-gw-* son Viewer y no alcanzan). Una de:
  * INFISICAL_ADMIN_TOKEN en el entorno (p. ej. un token de la "Instance Admin Identity", Token Auth, TTL corto), o
  * INFISICAL_IMPORT_CLIENT_ID + INFISICAL_IMPORT_CLIENT_SECRET (Universal Auth de una identity con rol Member/Admin
    en los proyectos destino), o
  * si no hay nada, lo pide por teclado (getpass). Nunca por argumento.

Formato de la selección (ver secret-import.selection.draft.yaml generado por el inventario):
  version: 1
  environment: prod
  secrets:
    - name: OPENAI_API_KEY                 # nombre en Infisical (y clave en secret-policy.yaml)
      enabled: true
      context: work                  # work | personal | side | shared
      folder: /                            # side: /shop | /blog | /mnemos
      source: {file: "~/code/my-app/.env", var: OPENAI_API_KEY}
      expect_same: ["~/code/my-app-tests/.env:OPENAI_API_KEY"]   # opcional
      description: "OpenAI de Work"
      policy:                              # opcional; sin esto el secreto queda solo como bóveda
        contexts: [work]             # obligatorio si context: shared (en qué secciones repetirlo)
        allowed_hosts: ["api.openai.com"]  # o allowed_hosts_from: SUPABASE_URL (var https del mismo archivo)
        methods: ["GET", "POST"]           # opcional
        inject: {header: Authorization, format: "Bearer {value}"}
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import ipaddress
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from hub_gateway.contexts import CONTEXTS as _CONTEXTS  # noqa: E402  (config/contexts.yaml)

CONTEXTS = {c: f"hub-{c}" for c in _CONTEXTS} | {"shared": "hub-shared"}
POLICY_CONTEXTS = tuple(_CONTEXTS)
# contextos con subproyectos: el folder de Infisical tiene que ser uno de sus proyectos
PROJECT_FOLDERS = {c: {f"/{p}" for p in spec.projects} for c, spec in _CONTEXTS.items() if spec.projects}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
PLACEHOLDER_RE = re.compile(r"(^<.*>$|^x{3,}|your[_-]|_here$|-here$|changeme|change_me|^replace|^todo$|^dummy|"
                            r"^placeholder|^\.\.\.$|^\*+$|^sk-\.\.\.|^sk-x+|^__)", re.IGNORECASE)
HOME = Path.home().resolve()


# ---------------------------------------------------------------- .env (valores solo en memoria)
def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        name, raw = m.group(1), m.group(2).strip()
        if raw[:1] in ('"', "'"):
            q, rest = raw[0], raw[1:]
            end = rest.find(q)
            if end >= 0:
                val = rest[:end]
            else:  # valor multilínea entre comillas (p. ej. una PEM)
                buf = [rest]
                while i < len(lines):
                    nxt = lines[i]
                    i += 1
                    e = nxt.find(q)
                    if e >= 0:
                        buf.append(nxt[:e])
                        break
                    buf.append(nxt)
                val = "\n".join(buf)
            if q == '"':
                val = val.replace("\\n", "\n")
        else:
            val = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        out[name] = val  # la última definición gana (como dotenv)
    return out


class EnvCache:
    def __init__(self) -> None:
        self._files: dict[Path, dict[str, str]] = {}

    def resolve(self, file: str) -> Path:
        p = Path(os.path.expanduser(file)).resolve()
        if HOME not in p.parents:
            raise ValueError(f"{file}: fuera de tu home")
        if p == (ROOT / ".env").resolve() or ROOT.resolve() in p.parents:
            raise ValueError(f"{file}: no se importa desde el propio repo mnemos")
        if re.search(r"(example|sample|template)", p.name, re.IGNORECASE):
            raise ValueError(f"{file}: es un archivo de ejemplo")
        return p

    def get(self, file: str, var: str) -> str | None:
        p = self.resolve(file)
        if p not in self._files:
            if not p.is_file():
                raise ValueError(f"{file}: no existe")
            self._files[p] = parse_env_file(p)
        return self._files[p].get(var)


def parse_ref(ref: str) -> tuple[str, str]:
    file, _, var = ref.rpartition(":")
    if not file or not NAME_RE.match(var):
        raise ValueError(f"referencia inválida {ref!r} (esperado 'archivo:VAR')")
    return file, var


# ---------------------------------------------------------------- selección
@dataclass
class Entry:
    name: str
    context: str
    folder: str
    file: str
    var: str
    description: str = ""
    expect_same: list[str] = field(default_factory=list)
    policy: dict | None = None
    # resuelto en validate()
    value: str | None = field(default=None, repr=False)
    hosts: list[str] = field(default_factory=list, repr=False)
    hosts_derived: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{CONTEXTS[self.context]}:{self.folder} {self.name}"


def load_selection(path: Path, only: set[str]) -> tuple[str, list[Entry]]:
    data = yaml.safe_load(path.read_text()) or {}
    env = data.get("environment", "prod")
    out = []
    for raw in data.get("secrets") or []:
        if not raw.get("enabled", False):
            continue
        if only and raw.get("name") not in only:
            continue
        src = raw.get("source") or {}
        out.append(Entry(name=str(raw.get("name", "")), context=str(raw.get("context", "")),
                         folder=str(raw.get("folder", "/")), file=str(src.get("file", "")), var=str(src.get("var", "")),
                         description=str(raw.get("description", "")), expect_same=list(raw.get("expect_same") or []),
                         policy=raw.get("policy")))
    return env, out


def host_ok(h: str) -> bool:
    if not h or "*" in h or "/" in h or ":" in h:
        return False
    try:
        ipaddress.ip_address(h)
        return False  # nada de IPs en la allowlist
    except ValueError:
        return "." in h


def validate(entries: list[Entry], cache: EnvCache) -> None:
    seen: dict[tuple[str, str, str], Entry] = {}
    for e in entries:
        err = e.errors
        if not NAME_RE.match(e.name):
            err.append("nombre inválido")
        if e.context not in CONTEXTS:
            err.append(f"contexto inválido {e.context!r}")
        if not e.folder.startswith("/"):
            err.append("folder debe empezar con /")
        if e.context in PROJECT_FOLDERS and e.folder not in PROJECT_FOLDERS[e.context]:
            err.append(f"en {e.context} el folder tiene que ser uno de {sorted(PROJECT_FOLDERS[e.context])}")
        k = (e.context, e.folder, e.name)
        if k in seen:
            err.append("duplicado en la selección (mismo contexto/carpeta/nombre)")
        seen[k] = e
        try:
            v = cache.get(e.file, e.var)
        except ValueError as ex:
            err.append(str(ex))
            continue
        if v is None:
            err.append(f"{e.var} no está en el archivo")
        elif v == "":
            err.append("valor vacío")
        elif PLACEHOLDER_RE.search(v):
            err.append("el valor parece un placeholder")
        else:
            e.value = v
        for ref in e.expect_same:
            try:
                f2, v2 = parse_ref(ref)
                other = cache.get(f2, v2)
            except ValueError as ex:
                err.append(str(ex))
                continue
            if other is None or e.value is None or not hmac.compare_digest(other.encode(), e.value.encode()):
                err.append(f"expect_same falló: {ref} no tiene el mismo valor")
        pol = e.policy
        if pol:
            ctxs = pol.get("contexts") or ([e.context] if e.context in POLICY_CONTEXTS else [])
            if not ctxs or any(c not in POLICY_CONTEXTS for c in ctxs):
                err.append("policy.contexts inválido (para shared es obligatorio)")
            if e.context in POLICY_CONTEXTS and ctxs != [e.context]:
                err.append("un secreto de contexto solo puede ir a la policy de su propio contexto")
            pol["contexts"] = ctxs
            if pol.get("allowed_hosts"):
                e.hosts = [h.strip().lower() for h in pol["allowed_hosts"]]
            elif pol.get("allowed_hosts_from"):
                try:
                    u = cache.get(e.file, str(pol["allowed_hosts_from"]))
                except ValueError as ex:
                    err.append(str(ex))
                    u = None
                sp = urlsplit(u or "")
                if sp.scheme != "https" or not sp.hostname:
                    err.append(f"allowed_hosts_from {pol['allowed_hosts_from']}: no es una URL https")
                else:
                    e.hosts, e.hosts_derived = [sp.hostname.lower()], True
            else:
                err.append("policy sin allowed_hosts ni allowed_hosts_from")
            if any(not host_ok(h) for h in e.hosts):
                err.append("allowed_hosts: solo hosts exactos (sin comodines, puertos, rutas ni IPs)")
            fmt = (pol.get("inject") or {}).get("format", "{value}")
            if "{value}" not in fmt:
                err.append("inject.format debe contener {value}")


# ---------------------------------------------------------------- Infisical
class Infisical:
    def __init__(self, base: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60,
                              transport=transport)

    @staticmethod
    def _msg(r: httpx.Response, secret: str | None = None) -> str:
        try:
            m = str(r.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            m = ""
        if secret and secret in m:
            m = "[mensaje omitido: contenía el valor]"
        return f"HTTP {r.status_code} {m}".strip()

    def project_ids(self) -> dict[str, str]:
        r = self.c.get("/api/v1/projects", params={"type": "secret-manager"})
        if r.status_code != 200:
            raise SystemExit(f"listar proyectos: {self._msg(r)}")
        return {p["slug"]: p["id"] for p in r.json().get("projects", []) if p.get("slug")}

    def ensure_folder(self, pid: str, env: str, folder: str) -> None:
        parent = "/"
        for part in [p for p in folder.split("/") if p]:
            r = self.c.post("/api/v1/folders", json={"workspaceId": pid, "projectId": pid, "environment": env,
                                                     "name": part, "path": parent})
            if r.status_code not in (200, 201, 400, 409):
                raise SystemExit(f"carpeta {folder}: {self._msg(r)}")
            parent = parent.rstrip("/") + "/" + part

    def upsert(self, pid: str, env: str, e: Entry, overwrite: bool) -> str:
        body = {"projectId": pid, "environment": env, "secretPath": e.folder, "secretValue": e.value,
                "secretComment": e.description, "type": "shared"}
        r = self.c.post(f"/api/v4/secrets/{e.name}", json=body)
        if r.status_code == 404:  # server viejo sin v4
            body3 = {**body, "workspaceId": pid}
            body3.pop("projectId")
            r = self.c.post(f"/api/v3/secrets/raw/{e.name}", json=body3)
        if r.status_code in (200, 201):
            return "creado"
        if r.status_code == 400 and "exist" in self._msg(r, e.value).lower():
            if not overwrite:
                return "ya existía (sin cambios; usá --overwrite)"
            pb = {"projectId": pid, "environment": env, "secretPath": e.folder, "secretValue": e.value, "type": "shared"}
            r2 = self.c.patch(f"/api/v4/secrets/{e.name}", json=pb)
            if r2.status_code == 404:
                pb3 = {**pb, "workspaceId": pid}
                pb3.pop("projectId")
                r2 = self.c.patch(f"/api/v3/secrets/raw/{e.name}", json=pb3)
            if r2.status_code in (200, 201):
                return "actualizado"
            return f"ERROR al actualizar: {self._msg(r2, e.value)}"
        return f"ERROR: {self._msg(r, e.value)}"


def get_token(base: str) -> str:
    tok = os.environ.get("INFISICAL_ADMIN_TOKEN")
    if tok:
        return tok
    cid, sec = os.environ.get("INFISICAL_IMPORT_CLIENT_ID"), os.environ.get("INFISICAL_IMPORT_CLIENT_SECRET")
    if cid and sec:
        r = httpx.post(f"{base}/api/v1/auth/universal-auth/login", json={"clientId": cid, "clientSecret": sec},
                       timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"login universal-auth: HTTP {r.status_code}")
        return r.json()["accessToken"]
    return getpass.getpass("Token de Infisical con permiso de escritura (no se guarda): ").strip()


def check_url(base: str) -> None:
    u = urlsplit(base)
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme != "https" and not local:
        raise SystemExit("--url tiene que ser https (o loopback)")


# ---------------------------------------------------------------- policy (solo nombres)
def policy_block(e: Entry, project_slug: str) -> str:
    pol = e.policy or {}
    inj = pol.get("inject") or {}
    lines = [f"  {e.name}:", f"    project: {project_slug}", f"    path: {e.folder}"]
    if e.description:
        lines.append(f"    description: {yaml.safe_dump(e.description, allow_unicode=True).strip().splitlines()[0]}")
    lines.append(f"    allowed_hosts: [{', '.join(repr(h).replace(chr(39), chr(34)) for h in e.hosts)}]")
    if pol.get("methods"):
        lines.append(f"    methods: [{', '.join(chr(34) + m.upper() + chr(34) for m in pol['methods'])}]")
    fmt = inj.get("format", "{value}").replace('"', '\\"')
    lines.append(f"    inject: {{ header: \"{inj.get('header', 'Authorization')}\", format: \"{fmt}\" }}")
    return "\n".join(lines)


def add_to_policy(text: str, ctx: str, blocks: list[str]) -> str:
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if re.match(rf"^{ctx}:\s*(\{{\s*\}})?\s*(#.*)?$", l)), None)
    if idx is None:
        lines += [f"{ctx}:"]
        idx = len(lines) - 1
    else:
        lines[idx] = f"{ctx}:"
    lines[idx + 1:idx + 1] = "\n".join(blocks).splitlines()
    return "\n".join(lines) + "\n"


def validate_policy_text(text: str) -> None:
    data = yaml.safe_load(text) or {}
    sys.path.insert(0, str(ROOT / "gateway" / "src"))
    try:
        from hub_gateway.secrets import parse_section  # misma validación que el gateway
    except ImportError:
        parse_section = None
    for ctx in POLICY_CONTEXTS:
        sec = data.get(ctx) or {}
        if not isinstance(sec, dict):
            raise SystemExit(f"policy: sección {ctx} inválida")
        if parse_section:
            parse_section(sec)


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selection", required=True, help="YAML revisado (sin valores)")
    ap.add_argument("--url", default="http://127.0.0.1:8082")
    ap.add_argument("--policy", default=str(ROOT / "config" / "secret-policy.yaml"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: valida y muestra el plan, no escribe nada")
    mode.add_argument("--apply", action="store_true", help="crea en Infisical y actualiza la policy")
    ap.add_argument("--overwrite", action="store_true", help="actualizar secretos que ya existen")
    ap.add_argument("--no-policy", action="store_true", help="no tocar secret-policy.yaml")
    ap.add_argument("--only", action="append", default=[], help="importar solo este nombre (repetible)")
    ap.add_argument("--show-derived-hosts", action="store_true")
    args = ap.parse_args()
    apply = args.apply
    base = args.url.rstrip("/")
    check_url(base)

    env, entries = load_selection(Path(args.selection), set(args.only))
    if not entries:
        raise SystemExit("No hay entradas con enabled: true en la selección.")
    cache = EnvCache()
    validate(entries, cache)
    good = [e for e in entries if not e.errors]
    bad = [e for e in entries if e.errors]

    print(f"{'APPLY' if apply else 'DRY-RUN'} · entorno {env} · {len(good)} ok · {len(bad)} con errores\n")
    for e in entries:
        src = f"{e.file}:{e.var}"
        if e.errors:
            print(f"  ✗ {e.label}  ← {src}\n      " + "\n      ".join(e.errors))
            continue
        extra = f" · expect_same ok ({len(e.expect_same)})" if e.expect_same else ""
        if e.policy:
            hs = ", ".join(e.hosts) if (not e.hosts_derived or args.show_derived_hosts) else \
                f"<host de {e.policy['allowed_hosts_from']}>"
            extra += f" · policy {'+'.join(e.policy['contexts'])} → [{hs}]"
        print(f"  ✓ {e.label}  ← {src} (valor presente){extra}")
    if bad:
        print("\nCorregí los errores (o poné enabled: false) antes de --apply.")
        if apply:
            raise SystemExit(1)
    if not apply:
        print("\nNada escrito. Repetí con --apply para crear en Infisical y actualizar la policy.")
        return

    ifs = Infisical(base, get_token(base))
    ids = ifs.project_ids()
    missing = {CONTEXTS[e.context] for e in good} - set(ids)
    if missing:
        raise SystemExit(f"proyectos inexistentes o sin acceso: {sorted(missing)} (¿corriste bootstrap_infisical.py?)")
    done: list[Entry] = []
    for e in good:
        pid = ids[CONTEXTS[e.context]]
        if e.folder != "/":
            ifs.ensure_folder(pid, env, e.folder)
        res = ifs.upsert(pid, env, e, args.overwrite)
        print(f"  {e.label}: {res}")
        if not res.startswith("ERROR"):
            done.append(e)
    for e in entries:
        e.value = None  # soltar los valores lo antes posible

    if args.no_policy:
        return
    ppath = Path(args.policy)
    text = ppath.read_text()
    current = yaml.safe_load(text) or {}
    per_ctx: dict[str, list[str]] = {}
    for e in done:
        if not e.policy:
            continue
        for ctx in e.policy["contexts"]:
            if e.name in (current.get(ctx) or {}):
                print(f"  policy {ctx}.{e.name}: ya estaba, no se toca")
                continue
            per_ctx.setdefault(ctx, []).append(policy_block(e, CONTEXTS[e.context]))
    if not per_ctx:
        print("policy: sin cambios")
        return
    for ctx, blocks in per_ctx.items():
        text = add_to_policy(text, ctx, blocks)
    validate_policy_text(text)
    ppath.write_text(text)
    print(f"policy: agregados {sum(map(len, per_ctx.values()))} nombres en {ppath} → revisá con git diff antes de commitear")


if __name__ == "__main__":
    main()
