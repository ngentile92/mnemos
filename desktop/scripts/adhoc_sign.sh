#!/usr/bin/env bash
# Ad-hoc sign Mnemos.app inside out: every Mach-O in the bundled Python / helpers, then the app itself, so the
# bundle is sealed (Contents/_CodeSignature). Without a sealed signature, macOS says a downloaded app
# "is damaged"; with an ad-hoc one it only shows the normal "unidentified developer" prompt.
# Usage: desktop/scripts/adhoc_sign.sh path/to/Mnemos.app   (macOS)
set -euo pipefail
app="$1"
xattr -cr "$app"
# precompile the bundled Python first: otherwise the first run writes __pycache__ into the signed bundle,
# which breaks the seal ("file added") and macOS treats the app as modified
py="$app/Contents/Resources/python/bin/python3"
if [ -x "$py" ]; then
  # and never write bytecode at run time (some packages, e.g. beartype, keep their own .pyc variants);
  # sitecustomize still loads under python -I
  sp=$("$py" -I -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
  grep -q "dont_write_bytecode" "$sp/sitecustomize.py" 2>/dev/null || \
    printf '\n# Mnemos: the app bundle is signed; writing __pycache__ inside it would break the signature\nimport sys\nsys.dont_write_bytecode = True\n' >> "$sp/sitecustomize.py"
  "$py" -I -m compileall -q -j 0 "$app/Contents/Resources/python/lib" >/dev/null || true
  "$py" -I -c "import sys; assert sys.dont_write_bytecode, 'sitecustomize not loaded'"
fi
n=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then codesign --force --sign - --timestamp=none "$f"; n=$((n+1)); fi
done < <(find "$app/Contents" -type f ! -path "*/_CodeSignature/*" -print0)
echo "signed $n Mach-O files"
codesign --force --sign - --timestamp=none "$app"
codesign --verify --deep --strict --verbose=2 "$app"
codesign -dv "$app" 2>&1 | grep -E "Identifier|Signature=adhoc|Sealed Resources"
