#!/usr/bin/env bash
# Builds dist/mnemos.mcpb (Claude Desktop extension, uv type: Claude installs Python deps itself).
#   scripts/build_mcpb.sh [git-ref]   default: current HEAD commit (must be pushed: the bundle installs it from GitHub)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:-$(git -C "$ROOT" rev-parse HEAD)}"
OUT="$ROOT/dist"; STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$ROOT/packaging/mcpb/." "$STAGE/"
sed -i.bak "s#mnemos@main#mnemos@$REF#" "$STAGE/pyproject.toml" && rm "$STAGE/pyproject.toml.bak"
mkdir -p "$OUT"
npx -y @anthropic-ai/mcpb validate "$STAGE/manifest.json"
npx -y @anthropic-ai/mcpb pack "$STAGE" "$OUT/mnemos.mcpb"
echo "built $OUT/mnemos.mcpb (mnemos-hub @ $REF)"
