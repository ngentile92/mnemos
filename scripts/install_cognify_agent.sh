#!/usr/bin/env bash
# Instala (o reinstala) el LaunchAgent que corre scripts/cognify_nightly.sh todos los días a las 03:47
# (procesa al grafo las memorias pendientes de todos los contextos; no borra nada).
# Uso: scripts/install_cognify_agent.sh [--run-now]
# Log: ~/Library/Logs/mnemos-cognify.log
set -euo pipefail
cd "$(dirname "$0")/.."
HUB_DIR="$PWD"
# Prefijo de los LaunchAgents (reverse-DNS): AIHUB_LABEL_PREFIX del entorno o de .env; default io.mnemos.
LABEL_PREFIX="${MNEMOS_LABEL_PREFIX:-${AIHUB_LABEL_PREFIX:-$(grep -E '^(MNEMOS|AIHUB)_LABEL_PREFIX=' ".env" 2>/dev/null | cut -d= -f2 | cut -d' ' -f1)}}"
LABEL_PREFIX="${LABEL_PREFIX:-io.mnemos}"
LABEL="$LABEL_PREFIX.cognify"
TEMPLATE_NAME="cognify"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -x .venv/bin/python3 ]] || { echo "Falta el venv del repo: python3 -m venv .venv && .venv/bin/pip install -e gateway"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__LABEL__#$LABEL_PREFIX#g" -e "s#__HUB_DIR__#$HUB_DIR#g" -e "s#__HOME__#$HOME#g" \
  scripts/launchd/$TEMPLATE_NAME.plist.template > "$DEST"
plutil -lint "$DEST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "LaunchAgent instalado: $DEST (todos los días 03:47)"

if [ "${1:-}" = "--run-now" ]; then
  launchctl kickstart "gui/$(id -u)/$LABEL"
  echo "Corriendo ahora; seguí el log con: tail -f ~/Library/Logs/mnemos-cognify.log"
fi
