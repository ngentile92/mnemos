#!/usr/bin/env bash
# Actualiza ESTA instancia a otra versión de Mnemos, con rollback automático si el smoke test falla.
#
#   scripts/update.sh                # último tag v* de origin
#   scripts/update.sh v0.2.0         # un tag, rama o commit
#   scripts/update.sh --dry-run [REF]  # muestra qué haría, sin tocar nada
#
# Pasos: git fetch → checkout del ref → render del compose (si .env usa COMPOSE_FILE=compose.generated.yaml)
# → pip install del gateway en .venv (lo usan dashboard/watchdog/scripts) → docker compose build → up -d
# → smoke test (scripts/smoke_test.py --quick, con reintentos). Si algo falla DESPUÉS del checkout: vuelve al
# ref anterior, rebuild, up -d y smoke de nuevo; sale con 1.
# Nunca corre `down` ni toca volúmenes, .env, cognee.env, config/ ni secrets/ (todo gitignored).
# Requiere el árbol versionado limpio (sin cambios en archivos trackeados).
#
# Variables (para tests o casos raros): MNEMOS_FORCE_BUILD=1 (rebuild aunque no cambie gateway/), MNEMOS_SMOKE_CMD (comando del smoke test), MNEMOS_SMOKE_TRIES (3),
# MNEMOS_SMOKE_WAIT (segundos entre intentos, 20), MNEMOS_SKIP_FETCH=1, MNEMOS_REMOTE (origin).
set -euo pipefail
cd "$(dirname "$0")/.."

# Todo va dentro de main(): bash lo parsea entero antes de correrlo, así el checkout puede reemplazar
# este mismo archivo sin romper la ejecución en curso.
main() {

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; shift; fi
REF="${1:-}"
REMOTE="${MNEMOS_REMOTE:-origin}"
TRIES="${MNEMOS_SMOKE_TRIES:-3}"
WAIT="${MNEMOS_SMOKE_WAIT:-20}"
if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi
SMOKE_CMD="${MNEMOS_SMOKE_CMD:-$PY scripts/smoke_test.py --quick}"

log() { echo "[update $(date +%H:%M:%S)] $*"; }
die() { log "ERROR: $*"; exit 1; }
envval() { [[ -f .env ]] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }

git rev-parse --git-dir >/dev/null 2>&1 || die "no es un checkout de git"
if ! git diff --quiet || ! git diff --cached --quiet; then
  die "hay cambios sin commitear en archivos versionados (git status); la config real va en archivos gitignored"
fi

PREV_SHA="$(git rev-parse HEAD)"
PREV_REF="$(git symbolic-ref -q --short HEAD || echo "$PREV_SHA")"

if [[ "${MNEMOS_SKIP_FETCH:-0}" != 1 ]]; then
  log "git fetch $REMOTE"
  git fetch -q --tags "$REMOTE"
fi
if [[ -z "$REF" ]]; then
  REF="$(git tag -l 'v*' --sort=-v:refname | head -1)"
  [[ -n "$REF" ]] || die "no hay tags v*; pasá un ref explícito"
fi
TARGET_SHA="$(git rev-parse --verify -q "${REF}^{commit}" || git rev-parse --verify -q "$REMOTE/${REF}^{commit}" || true)"
[[ -n "$TARGET_SHA" ]] || die "ref desconocido: $REF"

log "actual: $PREV_REF (${PREV_SHA:0:7}) → destino: $REF (${TARGET_SHA:0:7})"
if [[ "$DRY" == 1 ]]; then
  git log --oneline "$PREV_SHA..$TARGET_SHA" | head -30 || true
  log "dry-run: no cambié nada"
  exit 0
fi
if [[ "$PREV_SHA" == "$TARGET_SHA" ]]; then
  log "ya estás en $REF; igual reconstruyo y verifico"
fi

uses_generated() { [[ "${COMPOSE_FILE:-$(envval COMPOSE_FILE || true)}" == *compose.generated.yaml* ]]; }

deploy() {  # $1 = ref a desplegar. Ojo: dentro de `if` bash ignora set -e, por eso cada paso lleva `|| return 1`.
  local from
  from="$(git rev-parse HEAD)"
  if git show-ref -q --verify "refs/heads/$1"; then git checkout -q "$1" || return 1
  else git checkout -q --detach "$1" || return 1; fi
  if uses_generated; then
    log "render compose.generated.yaml"
    "$PY" scripts/render_compose.py || return 1
  fi
  if [[ -x .venv/bin/pip ]]; then
    log "pip install del gateway en .venv"
    # the distribution was renamed hub-gateway → mnemos-hub (same import package): drop the old name once
    if .venv/bin/pip show -q hub-gateway >/dev/null 2>&1; then .venv/bin/pip uninstall -q -y hub-gateway || true; fi
    rm -rf gateway/src/hub_gateway.egg-info   # stale metadata of the old name (pip would keep listing it)
    .venv/bin/pip install -q -e ./gateway || return 1
  fi
  # Cada `build` produce un image id nuevo aunque todo venga de caché (y eso recrea los gateways): solo se
  # reconstruye si cambió el código de las imágenes. Si falta una imagen, `up` la construye solo.
  if [[ "${MNEMOS_FORCE_BUILD:-0}" == 1 ]] || ! git diff --quiet "$from" HEAD -- gateway skills-sync; then
    log "docker compose build"
    docker compose build || return 1
  else
    log "sin cambios en gateway/ ni skills-sync/: no reconstruyo imágenes"
  fi
  log "docker compose up -d --wait (solo recrea lo que cambió; espera healthchecks)"
  docker compose up -d --wait --wait-timeout "${MNEMOS_WAIT_TIMEOUT:-180}" || return 1
}

smoke() {
  local i
  for ((i = 1; i <= TRIES; i++)); do
    log "smoke test ($i/$TRIES): $SMOKE_CMD"
    if bash -c "$SMOKE_CMD"; then return 0; fi
    ((i < TRIES)) && sleep "$WAIT"
  done
  return 1
}

restart_dashboard() {
  if [[ -x scripts/dashboard.sh ]] && scripts/dashboard.sh status >/dev/null 2>&1; then
    log "reinicio el dashboard"
    scripts/dashboard.sh restart >/dev/null 2>&1 || log "AVISO: no pude reiniciar el dashboard"
  fi
}

rollback() {
  log "FALLÓ: vuelvo a $PREV_REF (${PREV_SHA:0:7})"
  deploy "$PREV_REF" || log "AVISO: el deploy del rollback falló en algún paso"
  restart_dashboard
  if smoke; then
    log "rollback OK: la instancia quedó en $PREV_REF"
  else
    log "ROLLBACK SIN SMOKE OK: revisá a mano (docker compose ps, scripts/smoke_test.py --quick)"
  fi
  exit 1
}

if ! deploy "$TARGET_SHA"; then rollback; fi
restart_dashboard
if ! smoke; then rollback; fi
mkdir -p dev/state
printf '%s %s %s\n' "$(date +%Y-%m-%dT%H:%M:%S%z)" "$REF" "$TARGET_SHA" >> dev/state/updates.log
log "OK: instancia en $REF (${TARGET_SHA:0:7}). Para volver: scripts/update.sh ${PREV_SHA:0:12}"
}

main "$@"
exit
