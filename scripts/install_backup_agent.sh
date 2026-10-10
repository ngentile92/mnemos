#!/usr/bin/env bash
# Instala (o reinstala) el LaunchAgent que corre scripts/backup.sh todos los días a las 03:17.
# Uso: scripts/install_backup_agent.sh [--run-now]
# Log: ~/Library/Logs/mnemos-backup.log
set -euo pipefail
if [[ "$(uname -s)" == Linux ]]; then  # same job as a systemd user timer
  args=(--only backup)
  [[ "${1:-}" == --uninstall ]] && args+=(--uninstall)
  exec "$(dirname "$0")/install_systemd_units.sh" "${args[@]}"
fi
cd "$(dirname "$0")/.."
HUB_DIR="$PWD"
# Prefijo de los LaunchAgents (reverse-DNS): AIHUB_LABEL_PREFIX del entorno o de .env; default io.mnemos.
LABEL_PREFIX="${MNEMOS_LABEL_PREFIX:-${AIHUB_LABEL_PREFIX:-$(grep -E '^(MNEMOS|AIHUB)_LABEL_PREFIX=' ".env" 2>/dev/null | cut -d= -f2 | cut -d' ' -f1)}}"
LABEL_PREFIX="${LABEL_PREFIX:-io.mnemos}"
LABEL="$LABEL_PREFIX.backup"
TEMPLATE_NAME="backup"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

command -v restic >/dev/null || { echo "Falta restic: brew install restic"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__LABEL__#$LABEL_PREFIX#g" -e "s#__HUB_DIR__#$HUB_DIR#g" -e "s#__HOME__#$HOME#g" \
  scripts/launchd/$TEMPLATE_NAME.plist.template > "$DEST"
plutil -lint "$DEST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "LaunchAgent instalado: $DEST"

if [ "${1:-}" = "--run-now" ]; then
  launchctl kickstart "gui/$(id -u)/$LABEL"
  echo "Corriendo ahora; seguí el log con: tail -f ~/Library/Logs/mnemos-backup.log"
fi
