#!/usr/bin/env bash
# Can the app's private AI parts run inside a bundle a user got from the .dmg (quarantined, ad-hoc signed)?
#   desktop/scripts/check_bundle_ai.sh path/to/Mnemos.app
# Checks, with the bundled Python and nothing else on PATH: llama.cpp imports, a 1 MB test model loads and answers
# (Metal on Apple Silicon), the engine reports the built-in runtime, and the Apple Intelligence helper answers its
# probe (available or not, it must not crash). Exit 1 with the reason on the first failure.
set -euo pipefail
APP="$1"; PY="$APP/Contents/Resources/python/bin/python3"; T="$(mktemp -d)"
fail() { echo "BUNDLE AI CHECK FAILED: $*"; exit 1; }
[ -x "$PY" ] || fail "no bundled Python at $PY"
echo "quarantine on the bundle: $(xattr -p com.apple.quarantine "$APP" 2>/dev/null || echo none)"
curl -fsSL -o "$T/tiny.gguf" \
  "https://huggingface.co/ggml-org/models-moved/resolve/499bc8821c6b12b4e53c5bffcb21ec206f212d81/tinyllamas/stories260K.gguf"
echo "270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d  $T/tiny.gguf" | shasum -a 256 -c - >/dev/null \
  || fail "test model checksum"
env -i HOME="$T" PATH=/usr/bin:/bin "$PY" -I -B - "$T/tiny.gguf" <<'PY' || fail "built-in model runtime (see above)"
import sys
from pathlib import Path
from hub_gateway import local_engine as le
ok = le.runtime_ok()
print("runtime_ok:", ok, le.runtime_error() or "")
if not ok:
    sys.exit(1)
import llama_cpp
print("llama.cpp", llama_cpp.__version__)
m = llama_cpp.Llama(model_path=sys.argv[1], n_ctx=256, verbose=False)
out = m("Once upon a time", max_tokens=8, temperature=0)
print("test model answered:", repr(out["choices"][0]["text"][:40]))
helper = Path(sys.executable).resolve().parents[2] / "fm-helper"
print("apple helper:", "present" if helper.exists() else "missing", le.AppleLLM.probe(helper) if helper.exists() else "")
PY
echo "bundle AI check OK"
