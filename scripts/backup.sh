#!/usr/bin/env bash
# Backup del hub hacia un repo restic cifrado FUERA de la notebook.
# Requiere: restic (brew install restic) y RESTIC_REPOSITORY / RESTIC_PASSWORD en .env.
# Qué guarda: Vaultwarden (backup SQLite en caliente + adjuntos/keys), dump de Postgres de Infisical,
# volúmenes de Cognee (en frío: para Cognee unos segundos), estado OAuth de los gateways, .env, cognee.env,
# config/ y secrets/. Todo va cifrado por restic. Al final copia el repo a Google Drive (scripts/offsite_sync.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${COMPOSE_PROJECT_NAME:-mnemos}"
STAMP="$(date +%Y%m%d-%H%M%S)"
STAGE="backups/stage"
echo "== Inicio backup $STAMP"

envval() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$(envval RESTIC_REPOSITORY)}"
RESTIC_PASSWORD="${RESTIC_PASSWORD:-$(envval RESTIC_PASSWORD)}"
export RESTIC_REPOSITORY RESTIC_PASSWORD
: "${RESTIC_REPOSITORY:?definí RESTIC_REPOSITORY en .env}" "${RESTIC_PASSWORD:?definí RESTIC_PASSWORD en .env}"
command -v restic >/dev/null || { echo "Falta restic: brew install restic"; exit 1; }

rm -rf "$STAGE" && mkdir -p "$STAGE"

echo "== Vaultwarden (backup SQLite en caliente)"
docker compose exec -T vaultwarden /vaultwarden backup
# shellcheck disable=SC2012  # nombres generados por vaultwarden (db_<fecha>.sqlite3)
latest_vw="$(ls -t data/vaultwarden/db_*.sqlite3 | head -1)"
mv "$latest_vw" "$STAGE/vaultwarden-db.sqlite3"

echo "== Infisical (pg_dump)"
docker compose exec -T infisical-db pg_dump -U infisical -d infisical --clean --if-exists | gzip > "$STAGE/infisical.sql.gz"

echo "== Volúmenes (Cognee en frío + estado OAuth de gateways)"
docker compose stop cognee
trap 'docker compose start cognee >/dev/null' EXIT
for vol in cognee_system cognee_data gw_work gw_personal gw_side; do
  if docker volume inspect "${PROJECT}_${vol}" >/dev/null 2>&1; then
    docker run --rm -v "${PROJECT}_${vol}:/v:ro" -v "$PWD/$STAGE:/b" alpine:3.22 tar czf "/b/${vol}.tgz" -C /v .
  fi
done
docker compose start cognee && trap - EXIT

echo "== restic"
restic snapshots >/dev/null 2>&1 || restic init
restic backup --tag mnemos --host mnemos \
  "$STAGE" data/vaultwarden .env cognee.env config secrets \
  --exclude 'data/vaultwarden/db.sqlite3*' --exclude 'data/vaultwarden/tmp'
restic forget --tag mnemos --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
rm -rf "$STAGE"
echo "Backup OK ($STAMP). Probá un restore una vez por mes: scripts/restore.sh --check"

# copia offsite del repo (ya cifrado) a Google Drive; si falla, el backup local igual quedó OK
if [[ "${AIHUB_OFFSITE:-1}" != 0 ]]; then
  echo "== offsite (rclone → Google Drive)"
  scripts/offsite_sync.sh || echo "AVISO: offsite falló; ver ~/Library/Logs/mnemos-offsite.log (el backup local está OK)"
fi
