"""0.20: every English string appears once per table (a repeated key silently hides one of its translations)."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src/hub_gateway/i18n_es.py"


def test_no_repeated_keys():
    tree = ast.parse(SRC.read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            dups = sorted({k for k in keys if keys.count(k) > 1})
            assert not dups, f"{node.target.id}: {dups}"
