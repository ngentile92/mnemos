#!/usr/bin/env bash
# Put a relocatable Python (python-build-standalone) with mnemos-hub preinstalled into
# src-tauri/resources/python, so the release app needs nothing else installed (no uv, no Python).
# Usage: desktop/scripts/bundle_python.sh [path-to-gateway]   (run from anywhere; macOS arm64 or x86_64)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
gateway="$(cd "${1:-$here/../gateway}" && pwd)"
PBS_TAG="${PBS_TAG:-20251014}"
PY_VER="${PY_VER:-3.12.12}"
case "$(uname -m)" in arm64|aarch64) arch=aarch64 ;; *) arch=x86_64 ;; esac
case "$(uname -s)" in Darwin) triple="$arch-apple-darwin" ;; Linux) triple="$arch-unknown-linux-gnu" ;; *) echo "unsupported OS" >&2; exit 1 ;; esac
url="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PY_VER+$PBS_TAG-$triple-install_only_stripped.tar.gz"
out="$here/src-tauri/resources"
rm -rf "$out/python" && mkdir -p "$out"
echo "downloading $url"
curl -fsSL "$url" | tar -xz -C "$out"
py="$out/python/bin/python3"
"$py" -m pip install --no-cache-dir --disable-pip-version-check -q "$gateway"
# trim: tests, caches, headers, Tk/IDLE (nothing the hub uses)
find "$out/python" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$out/python/include" "$out/python/share" "$out/python/lib/python3."*/{test,idlelib,tkinter,turtledemo,ensurepip}
"$py" -I -c "import hub_gateway.local, fastmcp; print('bundled python ok:', fastmcp.__version__)"
du -sh "$out/python"
