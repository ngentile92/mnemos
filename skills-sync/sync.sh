#!/bin/sh
# Mantiene un clon del repo de skills en $DEST, actualizado cada $INTERVAL_SECONDS.
# Si GitHub no responde, se sigue sirviendo el último clon (el loop no se corta).
set -u
if [ -n "${SKILLS_DIR:-}" ]; then
  echo "SKILLS_DIR=$SKILLS_DIR: skills come from a local folder, nothing to sync (GitHub mode: unset SKILLS_DIR)"
  exec sleep 2147483647
fi
REPO_URL="${REPO_URL:?definí REPO_URL, ej. git@github.com:<usuario>/mnemos-skills.git}"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/skills}"
INTERVAL="${INTERVAL_SECONDS:-60}"
KEY="${DEPLOY_KEY_FILE:-/run/secrets/deploy_key}"

if [ -f "$KEY" ]; then
  # La key se copia para poder fijar permisos 600 (ssh rechaza keys legibles por otros).
  cp "$KEY" /tmp/deploy_key && chmod 600 /tmp/deploy_key
  # accept-new: fija la host key de github.com en el primer uso (TOFU) y falla si después cambia.
  export GIT_SSH_COMMAND="ssh -i /tmp/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/known_hosts"
fi

cd "$DEST" || { echo "no existe $DEST"; exit 1; }
git config --global --add safe.directory "$DEST"
if [ ! -d .git ]; then
  git init -q -b "$BRANCH" . && git remote add origin "$REPO_URL"
fi
git remote set-url origin "$REPO_URL"

last=""
while true; do
  if git fetch -q --depth 1 origin "$BRANCH" 2>/tmp/fetch.err; then
    git reset -q --hard FETCH_HEAD && git clean -qfdx
    head="$(git rev-parse --short HEAD)"
    if [ "$head" != "$last" ]; then
      echo "$(date -Iseconds) skills actualizadas a $head"
      last="$head"
    fi
  else
    echo "$(date -Iseconds) fetch falló, sigo con el clon actual: $(tr '\n' ' ' </tmp/fetch.err)"
  fi
  [ "${ONESHOT:-0}" = "1" ] && exit 0
  sleep "$INTERVAL"
done
