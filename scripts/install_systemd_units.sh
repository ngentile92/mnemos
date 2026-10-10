#!/usr/bin/env bash
# Linux equivalent of the macOS LaunchAgents: systemd *user* units for the watchdog (every 15 min),
# nightly backup (03:17), nightly cognify + hygiene (03:47) and the dashboard (always on, 127.0.0.1).
#   scripts/install_systemd_units.sh [--dry-run] [--dir DIR] [--uninstall] [--only watchdog,backup,cognify,dashboard]
# Logs: ${MNEMOS_LOG_DIR:-~/.local/state/mnemos}/mnemos-<name>.log (the dashboard reads them there).
# To keep timers running while you are logged out: sudo loginctl enable-linger "$USER"
set -euo pipefail
cd "$(dirname "$0")/.."
HUB_DIR="$PWD"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LOG_DIR="${MNEMOS_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/mnemos}"
PORT="${MNEMOS_DASHBOARD_PORT:-$(grep -E '^MNEMOS_DASHBOARD_PORT=' "$(cd "$(dirname "$0")/.." && pwd)/.env" 2>/dev/null | head -1 | cut -d= -f2 || true)}"
PORT="${PORT:-8790}"
DRY=""; ONLY="watchdog,backup,cognify,dashboard"; UNINSTALL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --dir) UNIT_DIR="$2"; shift ;;
    --only) ONLY="$2"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
PY="$HUB_DIR/.venv/bin/python3"
[[ -x "$PY" ]] || { [[ -n "$DRY" ]] || { echo "missing venv: python3 -m venv .venv && .venv/bin/pip install -e gateway" >&2; exit 1; }; }
want() { [[ ",$ONLY," == *",$1,"* ]]; }
PATHS="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"

service() {  # name, description, ExecStart, type(oneshot|simple)
  cat <<UNIT
[Unit]
Description=Mnemos $2
After=network-online.target docker.service

[Service]
Type=$4
WorkingDirectory=$HUB_DIR
Environment=PATH=$PATHS
Environment=MNEMOS_LOG_DIR=$LOG_DIR
ExecStart=$3
StandardOutput=append:$LOG_DIR/mnemos-$1.log
StandardError=append:$LOG_DIR/mnemos-$1.log
$( [[ "$4" == simple ]] && printf 'Restart=always\nRestartSec=5' )
$( [[ "$4" == simple ]] && printf '\n[Install]\nWantedBy=default.target' )
UNIT
}

timer() {  # name, OnCalendar or "" , OnUnitActiveSec or ""
  cat <<UNIT
[Unit]
Description=Mnemos $1 timer

[Timer]
$( [[ -n "$2" ]] && echo "OnCalendar=$2" )
$( [[ -n "$3" ]] && printf 'OnBootSec=2min\nOnUnitActiveSec=%s' "$3" )
Persistent=true

[Install]
WantedBy=timers.target
UNIT
}

write() {  # file, content
  if [[ -n "$DRY" ]]; then echo "--- $UNIT_DIR/$1"; echo "$2"; [[ "$UNIT_DIR" == "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user" ]] && return 0; fi
  mkdir -p "$UNIT_DIR"; printf '%s\n' "$2" > "$UNIT_DIR/$1"
}

UNITS=()
if want watchdog; then
  write mnemos-watchdog.service "$(service watchdog 'hub watchdog' "$PY $HUB_DIR/scripts/hub_watchdog.py" oneshot)"
  write mnemos-watchdog.timer "$(timer watchdog '' 15min)"; UNITS+=(mnemos-watchdog.timer)
fi
if want backup; then
  write mnemos-backup.service "$(service backup 'nightly backup' "/bin/bash $HUB_DIR/scripts/backup.sh" oneshot)"
  write mnemos-backup.timer "$(timer backup '*-*-* 03:17:00' '')"; UNITS+=(mnemos-backup.timer)
fi
if want cognify; then
  write mnemos-cognify.service "$(service cognify 'nightly cognify + hygiene' "/bin/bash $HUB_DIR/scripts/cognify_nightly.sh" oneshot)"
  write mnemos-cognify.timer "$(timer cognify '*-*-* 03:47:00' '')"; UNITS+=(mnemos-cognify.timer)
fi
if want dashboard; then
  write mnemos-dashboard.service "$(service dashboard 'dashboard (127.0.0.1 only)' "$PY $HUB_DIR/dashboard/server.py --host 127.0.0.1 --port $PORT" simple)"
  UNITS+=(mnemos-dashboard.service)
fi

if [[ -n "$UNINSTALL" ]]; then
  for u in "${UNITS[@]}"; do
    [[ -n "$DRY" ]] && { echo "would disable $u"; continue; }
    systemctl --user disable --now "$u" 2>/dev/null || true
    rm -f "$UNIT_DIR/${u%.*}.service" "$UNIT_DIR/${u%.*}.timer"
  done
  [[ -n "$DRY" ]] || systemctl --user daemon-reload
  echo "uninstalled: ${UNITS[*]}"; exit 0
fi
[[ -n "$DRY" ]] && { echo "dry run: nothing enabled (${UNITS[*]})"; exit 0; }
mkdir -p "$LOG_DIR"
command -v systemctl >/dev/null || { echo "systemctl not found: this needs a systemd Linux" >&2; exit 1; }
systemctl --user daemon-reload
for u in "${UNITS[@]}"; do systemctl --user enable --now "$u"; done
echo "installed in $UNIT_DIR: ${UNITS[*]}"
echo "logs: $LOG_DIR · status: systemctl --user list-timers 'mnemos-*'"
echo "keep running while logged out: sudo loginctl enable-linger $USER"
