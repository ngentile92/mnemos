#!/usr/bin/env bash
# Ad-hoc sign Mnemos.app inside out: every Mach-O in the bundled Python / helpers, then the app itself, so the
# bundle is sealed (Contents/_CodeSignature). Without a sealed signature, macOS says a downloaded app
# "is damaged"; with an ad-hoc one it only shows the normal "unidentified developer" prompt.
# Usage: desktop/scripts/adhoc_sign.sh path/to/Mnemos.app   (macOS)
set -euo pipefail
app="$1"
xattr -cr "$app"
n=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then codesign --force --sign - --timestamp=none "$f"; n=$((n+1)); fi
done < <(find "$app/Contents" -type f ! -path "*/_CodeSignature/*" -print0)
echo "signed $n Mach-O files"
codesign --force --sign - --timestamp=none "$app"
codesign --verify --deep --strict --verbose=2 "$app"
codesign -dv "$app" 2>&1 | grep -E "Identifier|Signature=adhoc|Sealed Resources"
