#!/usr/bin/env bash
# Corrida nocturna de cognify_pending.py (la lanza el LaunchAgent __LABEL__.cognify a las 03:47).
# Antes de empezar: espera a que no esté corriendo backup.sh (que para Cognee) y a que Cognee responda.
# Log: ~/Library/Logs/mnemos-cognify.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY=".venv/bin/python3"; [[ -x "$PY" ]] || PY="$(command -v python3)"
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
"$PY" -u scripts/cognify_pending.py --timeout "${MNEMOS_COGNIFY_TIMEOUT:-${AIHUB_COGNIFY_TIMEOUT:-5400}}"
rc=$?
echo "== Fin cognify $(date +%Y%m%d-%H%M%S) rc=$rc"

# Higiene de memoria (después del cognify): AIHUB_HYGIENE=report (default, solo lee y escribe el reporte en
# ~/mnemos-hygiene), llm (+ pistas de Ollama), apply (+ borra SOLO duplicados exactos) u off.
case "${MNEMOS_HYGIENE:-${AIHUB_HYGIENE:-report}}" in
  off) ;;
  report) "$PY" -u scripts/memory_hygiene.py || echo "higiene: falló (no afecta al cognify)";;
  llm) "$PY" -u scripts/memory_hygiene.py --llm "${MNEMOS_HYGIENE_MODEL:-${AIHUB_HYGIENE_MODEL:-llama3.1:8b}}" || echo "higiene: falló";;
  apply) "$PY" -u scripts/memory_hygiene.py --apply || echo "higiene: falló";;
  *) echo "AIHUB_HYGIENE desconocido: ${AIHUB_HYGIENE} (report|llm|apply|off)";;
esac
exit $rc
