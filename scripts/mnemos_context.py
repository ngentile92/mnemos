#!/usr/bin/env python3
"""Add, remove and list contexts without editing compose or .env by hand.

    python3 scripts/mnemos_context.py list
    python3 scripts/mnemos_context.py add research --description "papers and reading notes"
    python3 scripts/mnemos_context.py add lab --project robot --project drone
    python3 scripts/mnemos_context.py remove research            # shows the plan
    python3 scripts/mnemos_context.py remove research --yes

`add` / `remove` do the file work and nothing else:
  - config/contexts.yaml: adds/removes the context (and bridges that mention it); a timestamped backup is kept;
  - .env: adds the context's variables (generated keys filled in, __COMPLETAR__ for the OAuth app) and
    COMPOSE_FILE=compose.generated.yaml if missing; never deletes or rewrites existing lines (a backup is kept);
  - compose.generated.yaml: re-rendered with scripts/render_compose.py.

They never start or stop containers, never delete data volumes, and never touch GitHub, Tailscale, Cognee or
Infisical. Steps that need your accounts are printed at the end (OAuth app, bootstrap scripts, docker compose).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from init_env import gen  # noqa: E402

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


def env_suffix(name: str) -> str:
    return name.upper().replace("-", "_")


def ctx_env_vars(name: str) -> list[tuple[str, str, str]]:
    """(variable, value, comment) for one context. Generated secrets get a real value."""
    s = env_suffix(name)
    return [
        (f"GH_OAUTH_{s}_ID", "__COMPLETAR__", f'OAuth App "mnemos-{name}"'),
        (f"GH_OAUTH_{s}_SECRET", "__COMPLETAR__", ""),
        (f"HUB_JWT_SIGNING_KEY_{s}", gen(f"HUB_JWT_SIGNING_KEY_{s}"), ""),
        (f"HUB_STORAGE_KEY_{s}", gen(f"HUB_STORAGE_KEY_{s}"), "Fernet key"),
        (f"COGNEE_PW_{s}", gen(f"COGNEE_PW_{s}"), ""),
        (f"COGNEE_KEY_{s}", "", "written by scripts/bootstrap_cognee.py"),
        (f"INF_MI_{s}_ID", "", "written by scripts/bootstrap_infisical.py (optional)"),
        (f"INF_MI_{s}_SECRET", "", ""),
    ]


def env_keys(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^\s*([A-Z0-9_]+)=", text, re.M)}


def add_env(text: str, name: str) -> tuple[str, list[str]]:
    """Appends missing variables; returns (new text, names added). Existing lines are never changed."""
    have = env_keys(text)
    lines, added = [], []
    if "COMPOSE_FILE" not in have:
        lines.append("COMPOSE_FILE=compose.generated.yaml        # contexts other than work/personal/side")
        added.append("COMPOSE_FILE")
    for k, v, c in ctx_env_vars(name):
        if k not in have:
            lines.append(f"{k}={v}" + (f"   # {c}" if c else ""))
            added.append(k)
    if not lines:
        return text, []
    block = f"\n# --- context {name} (scripts/mnemos_context.py, {dt.date.today().isoformat()}) ---\n" + "\n".join(lines) + "\n"
    return (text.rstrip("\n") + "\n" + block) if text else block.lstrip("\n"), added


def add_context(cfg: dict[str, Any], name: str, description: str | None, projects: list[str]) -> dict[str, Any]:
    from hub_gateway.contexts import parse_bridges, parse_contexts
    if not NAME_RE.match(name) or name == "shared":
        raise SystemExit(f"invalid context name {name!r}: lower case letters, digits and '-', starting with a letter")
    cfg = dict(cfg)
    ctxs = dict(cfg.get("contexts") or {})
    if name in ctxs:
        raise SystemExit(f"context {name!r} already exists")
    spec: dict[str, Any] = {"description": description or name}
    if projects:
        for p in projects:
            if not PROJECT_RE.match(p):
                raise SystemExit(f"invalid project name {p!r}")
        spec["projects"] = {p: f"{name}_{p}".replace("-", "") for p in projects}
    else:
        spec["dataset"] = name.replace("-", "_")
    ctxs[name] = spec
    cfg["contexts"] = ctxs
    _, parsed = parse_contexts(cfg)  # validates names and dataset collisions
    names = [d for c in parsed.values() for d in c.own_dataset_names]
    if len(names) != len(set(names)):
        raise SystemExit("a dataset name of the new context collides with an existing one")
    parse_bridges(cfg, parsed)
    return cfg


def remove_context(cfg: dict[str, Any], name: str) -> tuple[dict[str, Any], int]:
    from hub_gateway.contexts import parse_contexts
    cfg = dict(cfg)
    ctxs = dict(cfg.get("contexts") or {})
    if name not in ctxs:
        raise SystemExit(f"no context named {name!r}")
    if len(ctxs) == 1:
        raise SystemExit("cannot remove the last context")
    del ctxs[name]
    cfg["contexts"] = ctxs
    bridges = cfg.get("bridges") or []
    kept = [b for b in bridges if isinstance(b, dict) and name not in (b.get("from"), b.get("to"))]
    if bridges:
        cfg["bridges"] = kept
    parse_contexts(cfg)
    return cfg, len(bridges) - len(kept)


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    b = path.with_name(f"{path.name}.bak-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, b)
    return b


def write_yaml(path: Path, cfg: dict[str, Any]) -> None:
    head = ("# Contexts of this Mnemos instance (managed with scripts/mnemos_context.py; hand edits are fine too).\n"
            "# See config/contexts.example.yaml for every option.\n")
    path.write_text(head + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def render(root: Path, contexts_file: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_compose.py"), "--base", str(root / "compose.yaml"),
                    "--out", str(root / "compose.generated.yaml"), "--contexts", str(contexts_file)], check=True)


def contexts_file(root: Path) -> Path:
    f = root / "config" / "contexts.yaml"
    if not f.exists():
        ex = root / "config" / "contexts.example.yaml"
        if not ex.exists():
            raise SystemExit(f"no {f} and no example to start from")
        shutil.copy2(ex, f)
        print(f"created {f} from contexts.example.yaml")
    return f


def next_steps(name: str, env: dict[str, str]) -> str:
    tailnet = env.get("TS_TAILNET") or "<tailnet>"
    host = f"https://hub-{name}.{tailnet}.ts.net"
    sd = env.get("SKILLS_DIR")
    skills_step = (f"Skills: mkdir -p {sd}/skills/{name} (or create them in the dashboard)" if sd
                   else f"In your skills repo: mkdir skills/{name} (and push).")
    return f"""
Next steps (they need your accounts, so this script does not do them):
  1. GitHub → Settings → Developer settings → OAuth Apps → New: name mnemos-{name}, homepage {host},
     callback {host}/auth/callback. Put the client id/secret in .env: GH_OAUTH_{env_suffix(name)}_ID / _SECRET.
  2. {skills_step}
  3. python3 scripts/bootstrap_cognee.py        # creates ctx-{name}, its dataset(s) and COGNEE_KEY_{env_suffix(name)}
  4. Optional, for secrets: python3 scripts/bootstrap_infisical.py ... and a `{name}:` section in config/secret-policy.yaml
  5. docker compose up -d                        # starts ts-{name} + gateway-{name}
  6. python3 scripts/smoke_test.py --quick, then add the connector {host}/mcp in your assistants."""


def read_env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^([A-Z0-9_]+)=([^#]*)", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--description")
    a.add_argument("--project", action="append", default=[], help="repeat for each project (makes `project` required)")
    a.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("remove")
    r.add_argument("name")
    r.add_argument("--yes", action="store_true", help="without it, only the plan is shown")
    args = ap.parse_args(argv)
    root: Path = args.root
    from hub_gateway.contexts import load_config, parse_bridges, parse_contexts

    if args.cmd == "list":
        f = root / "config" / "contexts.yaml"
        cfg = load_config(f) if f.exists() else load_config(root / "config" / "contexts.example.yaml")
        _, ctxs = parse_contexts(cfg)
        env = read_env(root / ".env")
        for c in ctxs.values():
            s = env_suffix(c.name)
            missing = [k for k, _, _ in ctx_env_vars(c.name)[:5] if env.get(k, "").startswith("__") or k not in env]
            print(f"{c.name:12} datasets: {', '.join(c.own_dataset_names):30} "
                  + (f".env missing: {', '.join(missing)}" if missing else ".env ok")
                  + ("" if env.get(f"COGNEE_KEY_{s}") else "  (no COGNEE_KEY yet)"))
        for b in parse_bridges(cfg, ctxs):
            print(f"bridge: {b.reader} reads {', '.join(b.datasets)} (from {b.source})")
        return 0

    cf = contexts_file(root)
    cfg = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
    env_path = root / ".env"
    if args.cmd == "add":
        new = add_context(cfg, args.name, args.description, args.project)
        env_text = env_path.read_text() if env_path.exists() else ""
        new_env, added = add_env(env_text, args.name)
        _, ctxs = parse_contexts(new)
        print(f"context {args.name}: datasets {', '.join(ctxs[args.name].own_dataset_names)}")
        print(f".env: add {', '.join(added) or 'nothing'}")
        if args.dry_run:
            print("dry run: nothing written")
            return 0
        b1 = backup(cf)
        write_yaml(cf, new)
        if added:
            backup(env_path)
            env_path.write_text(new_env)
            env_path.chmod(0o600)
        render(root, cf)
        print(f"wrote {cf}" + (f" (backup {b1.name})" if b1 else ""))
        print(next_steps(args.name, read_env(env_path)))
        return 0

    new, dropped = remove_context(cfg, args.name)
    s = env_suffix(args.name)
    print(f"remove context {args.name} from {cf}" + (f" and {dropped} bridge(s)" if dropped else ""))
    print("kept: its .env variables, its Cognee datasets and its Docker volumes (no data is deleted)")
    if not args.yes:
        print("plan only: re-run with --yes to apply")
        return 0
    b1 = backup(cf)
    write_yaml(cf, new)
    render(root, cf)
    print(f"wrote {cf} (backup {b1.name if b1 else '-'}); re-rendered compose.generated.yaml")
    print(f"""
Next steps:
  docker compose up -d --remove-orphans     # stops ts-{args.name} + gateway-{args.name}
  Remove the connector from your assistants and, if you want, the GitHub OAuth app mnemos-{args.name}.
  Its memory stays in Cognee (datasets of ctx-{args.name}) and in the volumes ts_{args.name} / gw_{args.name};
  delete them by hand only when you are sure. .env lines *_{s}* can stay or be removed by hand.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
