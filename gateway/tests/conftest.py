import json
import os
import textwrap
from pathlib import Path

import pytest

# Los tests usan siempre los contextos del ejemplo (no el config/contexts.yaml local de tu instancia).
os.environ.setdefault("HUB_CONTEXTS_FILE", str(Path(__file__).resolve().parents[2] / "config" / "contexts.example.yaml"))

DATASETS = {
    "shared": "00000000-0000-0000-0000-00000000000a",
    "work": "00000000-0000-0000-0000-0000000000c1",
    "personal": "00000000-0000-0000-0000-0000000000b1",
    "side_shop": "00000000-0000-0000-0000-0000000000d1",
    "side_blog": "00000000-0000-0000-0000-0000000000d2",
    "side_mnemos": "00000000-0000-0000-0000-0000000000d3",
}


def write_skill(root: Path, owner: str, name: str, description: str = "desc", metadata: dict | None = None,
                body: str = "Cuerpo", extra_files: dict | None = None) -> Path:
    d = root / "skills" / owner / name
    d.mkdir(parents=True, exist_ok=True)
    meta = ""
    if metadata:
        meta = "metadata:\n" + "".join(f'  {k}: "{v}"\n' for k, v in metadata.items())
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n{meta}---\n\n{body}\n", encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


@pytest.fixture
def skills_repo(tmp_path: Path) -> Path:
    root = tmp_path / "skills-repo"
    write_skill(root, "shared", "revisar-pr", "Checklist de PR")
    write_skill(root, "work", "review-cliente", "Review de clientes", {"hub-owner": "work"},
                extra_files={"references/checklist.md": "- item"})
    write_skill(root, "work", "propuesta-poc", "Propuestas POC", {"hub-share": "side"})
    write_skill(root, "personal", "rutina-gimnasio", "Rutina")
    write_skill(root, "side", "shop-deploy", "Deploy Shop")
    return root


@pytest.fixture
def datasets_file(tmp_path: Path) -> Path:
    p = tmp_path / "cognee-datasets.json"
    p.write_text(json.dumps({"datasets": DATASETS}))
    return p


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    p = tmp_path / "secret-policy.yaml"
    p.write_text(textwrap.dedent("""
        work:
          HUBSPOT_TOKEN:
            project: hub-work
            path: /
            description: "HubSpot CRM"
            allowed_hosts: ["api.hubapi.com"]
            inject: { header: "Authorization", format: "Bearer {value}" }
        personal:
          GITHUB_PAT:
            project: hub-personal
            path: /
            description: "GitHub personal"
            allowed_hosts: ["api.github.com"]
            methods: ["GET"]
            inject: { header: "Authorization", format: "Bearer {value}" }
        side:
          BLOG_OPENROUTER_KEY:
            project: hub-side
            path: /blog
            allowed_hosts: ["openrouter.ai"]
            inject: { header: "Authorization", format: "Bearer {value}" }
    """))
    return p


@pytest.fixture(autouse=True)
def _clear_dispute_cache():
    from hub_gateway import dispute
    dispute._CACHE.clear()
    yield
