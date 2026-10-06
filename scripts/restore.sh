#!/usr/bin/env bash
# Restore del hub desde restic.
#   scripts/restore.sh --check          verifica el repo y lista snapshots (no toca nada)
#   scripts/restore.sh [snapshot-id]    restaura (default: latest). PISA los datos actuales.
# En una máquina nueva: git clone mnemos, instalá Docker + restic, exportá RESTIC_REPOSITORY/RESTIC_PASSWORD
# (de Vaultwarden o del papel) y corré este script. Después: docker compose up -d.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${COMPOSE_PROJECT_NAME:-mnemos}"
envval() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$(envval RESTIC_REPOSITORY || true)}"
RESTIC_PASSWORD="${RESTIC_PASSWORD:-$(envval RESTIC_PASSWORD || true)}"
export RESTIC_REPOSITORY RESTIC_PASSWORD
: "${RESTIC_REPOSITORY:?exportá RESTIC_REPOSITORY}" "${RESTIC_PASSWORD:?exportá RESTIC_PASSWORD}"

if [ "${1:-}" = "--check" ]; then
  restic check && restic snapshots --tag mnemos
  exit 0
fi
SNAP="${1:-latest}"
read -r -p "Esto reemplaza Vaultwarden, Infisical, Cognee y el estado OAuth con el snapshot '$SNAP'. ¿Seguro? (escribí SI) " ok
[ "$ok" = "SI" ] || { echo "Cancelado."; exit 1; }

TMP="$(mktemp -d)"
restic restore "$SNAP" --tag mnemos --target "$TMP"
# restic guarda rutas absolutas de la máquina original: se ubica la raíz por el dump de Infisical.
DUMP="$(find "$TMP" -path '*backups/stage/infisical.sql.gz' | head -1)"
[ -n "$DUMP" ] || { echo "El snapshot no tiene backups/stage/infisical.sql.gz"; exit 1; }
SRC="$(cd "$(dirname "$DUMP")/../.." && pwd)"

echo "== archivos (.env, cognee.env, config, secrets, vaultwarden)"
cp "$SRC/.env" "$SRC/cognee.env" . && chmod 600 .env cognee.env   # primero: compose necesita el .env
docker compose down || true
cp -R "$SRC/config/." config/
mkdir -p secrets data && cp -R "$SRC/secrets/." secrets/ && chmod 700 secrets
rm -rf data/vaultwarden && cp -R "$SRC/data/vaultwarden" data/vaultwarden
cp "$SRC/backups/stage/vaultwarden-db.sqlite3" data/vaultwarden/db.sqlite3
rm -f data/vaultwarden/db.sqlite3-wal data/vaultwarden/db.sqlite3-shm

echo "== volúmenes"
for vol in cognee_system cognee_data gw_work gw_personal gw_side; do
  f="$SRC/backups/stage/${vol}.tgz"
  [ -f "$f" ] || continue
  docker volume rm "${PROJECT}_${vol}" >/dev/null 2>&1 || true
  docker volume create "${PROJECT}_${vol}" >/dev/null
  docker run --rm -v "${PROJECT}_${vol}:/v" -v "$(dirname "$f"):/b:ro" alpine:3.22 tar xzf "/b/${vol}.tgz" -C /v
done

echo "== Infisical (Postgres)"
docker compose up -d infisical-db
until docker compose exec -T infisical-db pg_isready -U infisical >/dev/null 2>&1; do sleep 2; done
gunzip -c "$SRC/backups/stage/infisical.sql.gz" | docker compose exec -T infisical-db psql -q -U infisical -d infisical

rm -rf "$TMP"
echo "Restore OK. Ahora: docker compose up -d && python3 scripts/smoke_test.py --quick"
