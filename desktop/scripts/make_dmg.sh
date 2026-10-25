#!/usr/bin/env bash
# Build the .dmg from the already signed app (the dmg Tauri makes is created before adhoc_sign.sh runs).
# Usage: desktop/scripts/make_dmg.sh path/to/Mnemos.app out.dmg   (macOS)
set -euo pipefail
app="$1"; out="$2"; s=$(mktemp -d)
ditto "$app" "$s/Mnemos.app"; ln -s /Applications "$s/Applications"
rm -f "$out"; hdiutil create -volname Mnemos -srcfolder "$s" -fs HFS+ -format UDZO -ov "$out"
rm -rf "$s"
