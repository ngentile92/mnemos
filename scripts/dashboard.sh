#!/usr/bin/env bash
# Dashboard en vivo del hub (solo lectura, solo 127.0.0.1). Nunca se expone por Funnel.
#   scripts/dashboard.sh start | stop | restart | status | open | logs | install-agent | uninstall-agent
#                        tailnet-on | tailnet-off
# Puerto: AIHUB_DASHBOARD_PORT (default 8787).
# Tailnet (opcional): `tailnet-on` publica el dashboard SOLO en el tailnet con `tailscale serve --bg` en
#   https://<esta-mac>.<tailnet>.ts.net:${MNEMOS_DASHBOARD_TS_PORT:-${AIHUB_DASHBOARD_TS_PORT:-8444}} (nunca Funnel; 443 = Vaultwarden,
#   8443 = Infisical). Ojo: la pestaña Explorar muestra texto de la memoria y nombres de secretos a
#   cualquier dispositivo de tu tailnet que llegue a esta Mac. `tailnet-off` lo saca.
#
# Dos modos, mismos comandos:
#   * manual (default): start lo lanza en su propia sesión y guarda el pid en dev/state/dashboard.pid.
#   * LaunchAgent (macOS, tras `install-agent`): arranca al iniciar sesión y launchd lo relanza si se cae
#     (KeepAlive). start/stop/restart/status pasan por launchctl; `stop` lo descarga hasta el próximo
#     `start` o el próximo login. `uninstall-agent` vuelve al modo manual.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${MNEMOS_DASHBOARD_PORT:-${AIHUB_DASHBOARD_PORT:-8787}}"
URL="http://127.0.0.1:${PORT}"
STATE="$ROOT/dev/state"
PIDFILE="$STATE/dashboard.pid"
if [[ -d "$HOME/Library/Logs" ]]; then LOG="$HOME/Library/Logs/mnemos-dashboard.log"; else LOG="${MNEMOS_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/mnemos}/mnemos-dashboard.log"; fi
PY="$ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"
# Prefijo de los LaunchAgents (reverse-DNS): AIHUB_LABEL_PREFIX del entorno o de .env; default io.mnemos.
LABEL_PREFIX="${MNEMOS_LABEL_PREFIX:-${AIHUB_LABEL_PREFIX:-$(grep -E '^(MNEMOS|AIHUB)_LABEL_PREFIX=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 | cut -d' ' -f1)}}"
LABEL_PREFIX="${LABEL_PREFIX:-io.mnemos}"
LABEL="$LABEL_PREFIX.dashboard"
TEMPLATE_NAME="dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
TS_PORT="${MNEMOS_DASHBOARD_TS_PORT:-${AIHUB_DASHBOARD_TS_PORT:-8444}}"

has_launchctl() { command -v launchctl >/dev/null; }
# Linux: user unit from scripts/install_systemd_units.sh
SD_UNIT="$HOME/.config/systemd/user/mnemos-dashboard.service"
sd_installed() { command -v systemctl >/dev/null && [[ -f "$SD_UNIT" ]]; }
agent_installed() { has_launchctl && [[ -f "$PLIST" ]]; }
agent_loaded() { agent_installed && launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }
agent_pid() { launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}'; }
manual_running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
current_pid() {
  if agent_loaded; then agent_pid; elif manual_running; then cat "$PIDFILE"; fi
}
running() { local p; p="$(current_pid)"; [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; }
mode() { if agent_installed; then echo "LaunchAgent $LABEL"; else echo "manual"; fi; }

wait_up() {
  for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "$URL/"; then echo "dashboard en $URL (pid $(current_pid), $(mode), log $LOG)"; return 0; fi
    sleep 0.5
  done
  echo "no arrancó; mirá $LOG" >&2
  return 1
}

stop_manual() {
  if manual_running; then kill "$(cat "$PIDFILE")" && echo "dashboard (manual) detenido"; fi
  rm -f "$PIDFILE"
}

start() {
  if sd_installed; then stop_manual; systemctl --user start mnemos-dashboard.service; wait_up; return; fi
  if running; then echo "ya corre (pid $(current_pid), $(mode)) → $URL"; return 0; fi
  mkdir -p "$STATE" "$(dirname "$LOG")"
  if agent_installed; then
    stop_manual
    if agent_loaded; then launchctl kickstart "$DOMAIN/$LABEL"; else launchctl bootstrap "$DOMAIN" "$PLIST"; fi
    wait_up
    return
  fi
  # sesión propia (setsid) para que sobreviva a la terminal / al proceso que lo lanzó
  AIHUB_DASHBOARD_PORT="$PORT" nohup "$PY" -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
    "$PY" "$ROOT/dashboard/server.py" --host 127.0.0.1 --port "$PORT" </dev/null >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  if wait_up; then return 0; fi
  rm -f "$PIDFILE"
  return 1
}

stop() {
  local did=""
  if sd_installed; then systemctl --user stop mnemos-dashboard.service && did=1; fi
  if agent_loaded; then
    launchctl bootout "$DOMAIN/$LABEL" && did=1
    echo "dashboard detenido (LaunchAgent descargado; vuelve con start o en el próximo login)"
  fi
  if manual_running; then stop_manual; did=1; fi
  rm -f "$PIDFILE"
  [[ -n "$did" ]] || echo "no estaba corriendo"
}

restart() {
  if sd_installed; then systemctl --user restart mnemos-dashboard.service; sleep 1; wait_up; return; fi
  if agent_loaded; then
    launchctl kickstart -k "$DOMAIN/$LABEL"
    sleep 1
    wait_up
  else
    stop; start
  fi
}

install_agent() {
  has_launchctl || { echo "install-agent es solo para macOS (launchd)" >&2; exit 1; }
  local tpl="$ROOT/scripts/launchd/$TEMPLATE_NAME.plist.template"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$STATE"
  stop_manual
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  sed -e "s#__LABEL__#$LABEL_PREFIX#g" -e "s#__HUB_DIR__#$ROOT#g" -e "s#__HOME__#$HOME#g" -e "s#__PY__#$PY#g" -e "s#__PORT__#$PORT#g" "$tpl" >"$PLIST"
  plutil -lint "$PLIST"
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "LaunchAgent instalado: $PLIST (arranca al iniciar sesión, KeepAlive)"
  wait_up
}

uninstall_agent() {
  has_launchctl || { echo "no hay launchd acá" >&2; exit 1; }
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "LaunchAgent desinstalado; el dashboard quedó detenido (scripts/dashboard.sh start para el modo manual)"
}

ts_cli() {
  local c
  for c in tailscale /usr/local/bin/tailscale /opt/homebrew/bin/tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  return 1
}

# línea de `tailscale serve status` para nuestro puerto (vacía si no está publicado)
ts_line() { "$1" serve status 2>/dev/null | grep -E "^https://[^ ]+:${TS_PORT}( |$)" || true; }

tailnet_on() {
  local ts line
  ts="$(ts_cli)" || { echo "no encuentro el CLI de tailscale" >&2; exit 1; }
  if ! [[ "$TS_PORT" =~ ^[0-9]+$ ]] || [[ "$TS_PORT" == 443 || "$TS_PORT" == 8443 ]]; then
    echo "AIHUB_DASHBOARD_TS_PORT=$TS_PORT inválido (443 y 8443 son de Vaultwarden/Infisical)" >&2; exit 1
  fi
  # el dashboard lee el nombre MagicDNS al arrancar: reiniciar para que acepte el Host del tailnet
  if running; then restart; else start; fi
  "$ts" serve --bg --https="$TS_PORT" "http://127.0.0.1:$PORT" >/dev/null
  line="$(ts_line "$ts")"
  if [[ "$line" != *"(tailnet only)"* ]]; then
    "$ts" serve --https="$TS_PORT" off >/dev/null 2>&1 || true
    echo "tailscale serve no quedó 'tailnet only' (¿Funnel?): lo saqué. Revisá: $ts serve status" >&2
    exit 1
  fi
  echo "dashboard en el tailnet: ${line%% *}  (solo tailnet, sin Funnel)"
}

tailnet_off() {
  local ts
  ts="$(ts_cli)" || { echo "no encuentro el CLI de tailscale" >&2; exit 1; }
  if [[ -n "$(ts_line "$ts")" ]]; then "$ts" serve --https="$TS_PORT" off; echo "dashboard fuera del tailnet (:$TS_PORT)"; else echo "no estaba publicado en :$TS_PORT"; fi
}

tailnet_status() {
  local ts line
  ts="$(ts_cli)" || return 0
  line="$(ts_line "$ts")"
  [[ -n "$line" ]] && echo "tailnet: ${line%% *} ${line#* }"
  return 0
}

open_url() {
  if command -v open >/dev/null; then open "$URL"; elif command -v xdg-open >/dev/null; then xdg-open "$URL"; else echo "$URL"; fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) if running; then echo "corriendo (pid $(current_pid), $(mode)) → $URL"; tailnet_status; else echo "detenido ($(mode))"; exit 1; fi ;;
  open) running || start; open_url ;;
  logs) tail -n 50 "$LOG" ;;
  install-agent) install_agent ;;
  uninstall-agent) uninstall_agent ;;
  tailnet-on) tailnet_on ;;
  tailnet-off) tailnet_off ;;
  *) echo "uso: $0 start|stop|restart|status|open|logs|install-agent|uninstall-agent|tailnet-on|tailnet-off" >&2; exit 2 ;;
esac
