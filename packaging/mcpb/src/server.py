"""Claude Desktop entry point: Mnemos local mode over stdio, one context per install."""
import os
import sys

from hub_gateway import local

home = local.home_dir(os.environ.get("MNEMOS_HOME"))
ctx = (os.environ.get("MNEMOS_CONTEXT") or "personal").strip().lower()
if not (home / "contexts.yaml").exists():
    local.init_home(home, [ctx])  # first run: create the folder with this one context
sys.exit(local.main(["stdio", "--home", str(home), "--context", ctx]))
