#!/usr/bin/env bash
# Prepara la notebook para `docker compose up`. Idempotente: se puede correr las veces que quieras.
set -euo pipefail
cd "$(dirname "$0")/.."

need() { command -v "$1" >/dev/null 2>&1 || { echo "Falta $1. $2"; exit 1; }; }
need docker "Instalá/abrí Docker Desktop."
need python3 "Instalá Python 3.11+."
docker info >/dev/null 2>&1 || { echo "El daemon de Docker no corre: abrí Docker Desktop y reintentá."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Falta Docker Compose v2."; exit 1; }

python3 scripts/init_env.py

# Config de tu instancia (gitignoreada): se crea desde los ejemplos si no existe.
for f in contexts secret-policy; do
  [ -f "config/$f.yaml" ] || { cp "config/$f.example.yaml" "config/$f.yaml"; echo "Creé config/$f.yaml desde el ejemplo: editalo."; }
done

mkdir -p data/vaultwarden secrets backups
chmod 700 secrets
if [ ! -f secrets/skills_deploy_key ]; then
  ssh-keygen -t ed25519 -N "" -C "mnemos skills-sync (read-only)" -f secrets/skills_deploy_key >/dev/null
  echo
  echo "Generé secrets/skills_deploy_key. Cargá ESTA clave pública como Deploy key (read-only) en"
  gh_user="$(grep -E '^GITHUB_USER=' .env | cut -d= -f2 | cut -d' ' -f1)"
  [[ -n "$gh_user" && "$gh_user" != "__COMPLETAR__" ]] || gh_user="<YOUR_GITHUB_USER>"
  echo "https://github.com/$gh_user/<your-skills-repo>/settings/keys :"
  cat secrets/skills_deploy_key.pub
fi
[ -f config/cognee-datasets.json ] || echo "Recordá: después de levantar Cognee corré  python3 scripts/bootstrap_cognee.py"
echo
echo "Siguiente: docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync"
