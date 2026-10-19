#!/usr/bin/env bash
# Corrida nocturna de cognify_pending.py (la lanza el LaunchAgent __LABEL__.cognify a las 03:47).
# Antes de empezar: espera a que no esté corriendo backup.sh (que para Cognee) y a que Cognee responda.
# Log: ~/Library/Logs/mnemos-cognify.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# Without the repo venv (e.g. a Linux server without python3-venv), run the scripts inside the gateway image, which
# already has every dependency. --network host: Cognee (and Ollama) on 127.0.0.1; reports go to ~/mnemos-hygiene.
PY=(.venv/bin/python3)
if [[ ! -x "${PY[0]}" ]]; then
  if docker image inspect mnemos/gateway:local >/dev/null 2>&1; then
    mkdir -p "$HOME/mnemos-hygiene"
    PY=(docker run --rm --user "$(id -u):$(id -g)" --network host -e HOME="$HOME"
        -v "$PWD:$PWD:ro" -v "$HOME/mnemos-hygiene:$HOME/mnemos-hygiene" -w "$PWD"
        --entrypoint python mnemos/gateway:local)
  else
    PY=("$(command -v python3)")
  fi
fi
echo "== Inicio cognify $(date +%Y%m%d-%H%M%S)"
# si la Mac despertó y launchd disparó backup y cognify juntos, darle tiempo al backup a aparecer
sleep "${MNEMOS_COGNIFY_GRACE:-${AIHUB_COGNIFY_GRACE:-60}}"
for _ in $(seq 1 120); do                     # hasta 60 min esperando al backup
  pgrep -f "scripts/backup.sh" >/dev/null || break
  sleep 30
done
if pgrep -f "scripts/backup.sh" >/dev/null; then echo "backup sigue corriendo después de 60 min: salteo esta noche"; exit 0; fi
ok=""
for _ in $(seq 1 40); do                      # hasta 10 min esperando a Cognee (Docker puede estar arrancando)
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8010/health)" == 200 ]] && { ok=1; break; }
  sleep 15
done
[[ -n "$ok" ]] || { echo "Cognee no responde en 127.0.0.1:8010: salteo esta noche"; exit 1; }
"${PY[@]}" -u scripts/cognify_pending.py --timeout "${MNEMOS_COGNIFY_TIMEOUT:-${AIHUB_COGNIFY_TIMEOUT:-5400}}"
rc=$?
echo "== Fin cognify $(date +%Y%m%d-%H%M%S) rc=$rc"

# Higiene de memoria (después del cognify): AIHUB_HYGIENE=report (default, solo lee y escribe el reporte en
# ~/mnemos-hygiene), llm (+ pistas de Ollama), apply (+ borra SOLO duplicados exactos) u off.
# MNEMOS_HYGIENE / MNEMOS_HYGIENE_MODEL: from the environment or, if unset, from .env (the LaunchAgent/systemd unit
# does not pass them).
envval() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
[[ -n "${MNEMOS_HYGIENE:-}${AIHUB_HYGIENE:-}" ]] || MNEMOS_HYGIENE="$(envval '(MNEMOS|AIHUB)_HYGIENE')"
[[ -n "${MNEMOS_HYGIENE_MODEL:-}${AIHUB_HYGIENE_MODEL:-}" ]] || MNEMOS_HYGIENE_MODEL="$(envval '(MNEMOS|AIHUB)_HYGIENE_MODEL')"
case "${MNEMOS_HYGIENE:-${AIHUB_HYGIENE:-report}}" in
  off) ;;
  report) "${PY[@]}" -u scripts/memory_hygiene.py || echo "higiene: falló (no afecta al cognify)";;
  llm) "${PY[@]}" -u scripts/memory_hygiene.py --llm "${MNEMOS_HYGIENE_MODEL:-${AIHUB_HYGIENE_MODEL:-llama3.1:8b}}" || echo "higiene: falló";;
  apply) "${PY[@]}" -u scripts/memory_hygiene.py --apply || echo "higiene: falló";;
  *) echo "MNEMOS_HYGIENE desconocido: ${MNEMOS_HYGIENE:-${AIHUB_HYGIENE:-}} (report|llm|apply|off)";;
esac
exit $rc
