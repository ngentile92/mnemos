#!/usr/bin/env bash
# Instala (o reinstala) el LaunchAgent que corre scripts/hub_watchdog.py cada 15 minutos.
# Uso: scripts/install_watchdog_agent.sh [--run-now] [--uninstall]
# Log: ~/Library/Logs/mnemos-watchdog.log
# Status (dashboard): ~/Library/Logs/mnemos-watchdog-status.json
set -euo pipefail
cd "$(dirname "$0")/.."
HUB_DIR="$PWD"
# Prefijo de los LaunchAgents (reverse-DNS): AIHUB_LABEL_PREFIX del entorno o de .env; default io.mnemos.
LABEL_PREFIX="${AIHUB_LABEL_PREFIX:-$(grep -E '^AIHUB_LABEL_PREFIX=' ".env" 2>/dev/null | cut -d= -f2 | cut -d' ' -f1)}"
LABEL_PREFIX="${LABEL_PREFIX:-io.mnemos}"
LABEL="$LABEL_PREFIX.watchdog"
TEMPLATE_NAME="watchdog"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "LaunchAgent desinstalado: $LABEL"
  echo "Status/log quedan en ~/Library/Logs/mnemos-watchdog*. {log,status.json} (borralos a mano si querés)."
}

if [ "${1:-}" = "--uninstall" ]; then
  uninstall
  exit 0
fi

PYTHON="$HUB_DIR/.venv/bin/python3"
[[ -x "$PYTHON" ]] || { echo "Falta el venv del repo: python3 -m venv .venv && .venv/bin/pip install -e gateway"; exit 1; }
command -v docker >/dev/null || { echo "Falta docker (para recrear sidecars si hace falta)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$HOME/Library/Application Support/mnemos"
sed -e "s#__LABEL__#$LABEL_PREFIX#g" -e "s#__HUB_DIR__#$HUB_DIR#g" -e "s#__HOME__#$HOME#g" -e "s#__PYTHON__#$PYTHON#g" \
  scripts/launchd/$TEMPLATE_NAME.plist.template > "$DEST"
plutil -lint "$DEST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
echo "LaunchAgent instalado: $DEST (cada 15 min + al cargar)"
echo "Log:    ~/Library/Logs/mnemos-watchdog.log"
echo "Status: ~/Library/Logs/mnemos-watchdog-status.json"
echo "Desinstalar: scripts/install_watchdog_agent.sh --uninstall"

if [ "${1:-}" = "--run-now" ]; then
  launchctl kickstart "$DOMAIN/$LABEL"
  echo "Corriendo ahora; seguí el log con: tail -f ~/Library/Logs/mnemos-watchdog.log"
fi
