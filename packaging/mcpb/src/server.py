"""Claude Desktop entry point: Mnemos local mode over stdio.

Context "all" (default) or empty = one connector for every space (Claude uses its default space and can pass
`context`); a space name = that space only, as in 0.5 and earlier.
"""
import os
import sys

from hub_gateway import local

home = local.home_dir(os.environ.get("MNEMOS_HOME") or None)
ctx = (os.environ.get("MNEMOS_CONTEXT") or "").strip().lower()
if ctx.startswith("${") or ctx in ("", "all", "*"):  # unset user_config placeholders count as empty
    ctx = ""
if not (home / "contexts.yaml").exists():
    local.init_home(home, [ctx or "personal"])  # first run: create the folder
sys.exit(local.main(["stdio", "--home", str(home)] + (["--context", ctx] if ctx else [])))
