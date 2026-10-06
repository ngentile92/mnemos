#!/usr/bin/env bash
# Copia offsite del repo restic local (RESTIC_REPOSITORY) a Google Drive con rclone.
# La corre backup.sh al final (03:17 diario) y también se puede correr a mano: scripts/offsite_sync.sh
# El repo restic ya está cifrado: a Drive sólo suben blobs cifrados (sin password no se puede leer nada).
# Remote: AIHUB_OFFSITE_REMOTE (default gdrive:mnemos-backups). Crear una vez con:
#   rclone config create gdrive drive scope=drive.file     # abre el browser para OAuth; sólo ve lo que rclone crea
# rclone.conf (token OAuth) vive en ~/.config/rclone/rclone.conf: NUNCA al repo.
# Log: ~/Library/Logs/mnemos-offsite.log (el dashboard lee "== Inicio offsite" / "Offsite OK").
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REMOTE="${AIHUB_OFFSITE_REMOTE:-gdrive:mnemos-backups}"
if [[ -d "$HOME/Library/Logs" ]]; then LOG="$HOME/Library/Logs/mnemos-offsite.log"; else LOG="dev/state/offsite.log"; fi
LOCK="dev/state/offsite-sync.lock"
mkdir -p dev/state "$(dirname "$LOG")"
STAMP="$(date +%Y%m%d-%H%M%S)"

envval() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
REPO="${RESTIC_REPOSITORY:-$(envval RESTIC_REPOSITORY)}"

log() { echo "$*" | tee -a "$LOG"; }
fail() { log "Offsite FAIL ($STAMP): $*"; exit 1; }

log "== Inicio offsite $STAMP"
command -v rclone >/dev/null || fail "falta rclone (brew install rclone)"
[[ -n "$REPO" && -f "$REPO/config" ]] || fail "RESTIC_REPOSITORY no es un repo restic local"
rclone listremotes 2>/dev/null | grep -qx "${REMOTE%%:*}:" || fail "no existe el remote ${REMOTE%%:*} (ver cabecera del script)"

# lock simple (mkdir es atómico); si quedó uno viejo (>6 h) de una corrida muerta, se descarta
if ! mkdir "$LOCK" 2>/dev/null; then
  if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]]; then rm -rf "$LOCK"; mkdir "$LOCK" || fail "lock"; else fail "ya hay un offsite_sync corriendo ($LOCK)"; fi
fi
trap 'rm -rf "$LOCK"' EXIT
if [[ -d "$REPO/locks" ]] && [[ -n "$(ls -A "$REPO/locks" 2>/dev/null)" ]]; then
  log "aviso: el repo restic tiene locks (¿restic corriendo?); sincronizo igual, la próxima corrida completa"
fi

# sync: lo borrado por prune va a la papelera de Drive (30 días), red extra ante borrados accidentales.
# --max-duration: si la subida es enorme, corta prolijo y sigue mañana (no bloquea al cognify de las 03:47).
rclone sync "$REPO" "$REMOTE" --max-duration "${AIHUB_OFFSITE_MAX:-40m}" --transfers 4 --stats 10m -v 2>&1 \
  | grep -E 'NOTICE|ERROR|Transferred:|Deleted:|Checks:|Elapsed' | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[[ "$rc" == 0 ]] || fail "rclone sync salió con error (rc=$rc)"
# verificación: tamaños + MD5 (Drive guarda MD5) de todo lo local contra el remote
rclone check "$REPO" "$REMOTE" --one-way --quiet 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[[ "$rc" == 0 ]] || fail "rclone check encontró diferencias"
size="$(rclone size "$REMOTE" 2>/dev/null | awk -F': ' '/Total size/{print $2}')"
log "Offsite OK ($STAMP) → $REMOTE · $size"
