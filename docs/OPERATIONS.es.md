# Mnemos — operación (español)

Referencia de operación del día a día: tools, pruebas locales, grafo, dashboard, cognify nocturno, higiene,
watchdog, backups. El setup inicial paso a paso está en [`SETUP.es.md`](SETUP.es.md).

> Los LaunchAgents (`install_*_agent.sh`, `dashboard.sh install-agent`) y las notificaciones del watchdog son
> **solo macOS y opcionales**. Usan el prefijo `AIHUB_LABEL_PREFIX` (default `io.mnemos`; abajo aparece así).
> En Linux, el equivalente es un timer de systemd o una línea de cron que llame al mismo script (ver README,
> *Scheduling on Linux*).

## Tools que exponen los gateways

| Tool | Qué hace | Límite |
|---|---|---|
| `hub_whoami` | Contexto, datasets y proyectos visibles | – |
| `memory_search` | Busca en la memoria del contexto (+ `shared` si `include_shared`) | Solo datasets del contexto; no usa LLM |
| `memory_save` | Guarda un hecho en el dataset del contexto o en `shared` | En `side` exige `project` |
| `memory_search` `mode` | `auto` (default): búsqueda local híbrida en `search.sqlite` (BM25 + embeddings de Ollama si `HUB_EMBED_URL`/`HUB_EMBED_MODEL`), y el grafo de Cognee solo si no encuentra nada; `graph`, `keyword`, `semantic`, `hybrid` para forzar | Índice local por gateway, sincronizado desde Cognee (solo baja notas nuevas, cada ≤60 s); nunca mezcla datasets de otros contextos |
| `memory_answer` | Respuesta corta escrita por un modelo **local** (Ollama, `HUB_ANSWER_MODEL`) solo con las notas encontradas, con citas (id de cada nota) y `known=false` + `unknown` cuando la memoria no lo sabe | Desactivada si no hay modelo; una respuesta sin citas válidas se marca como no sabida |
| `memory_list` | Lista notas con **id**, dataset, fecha, app de origen y texto (filtro `contains`, `project`) | Datasets del contexto (+ `shared` solo lectura con `include_shared`) |
| `memory_update` | Corrige una nota propia en el lugar (`PATCH` de Cognee: mismo id y fecha; la versión anterior queda en el historial) | Solo datasets **propios** del contexto; nunca `shared` |
| `memory_delete` | Retira una nota propia (y lo que el grafo sacó solo de ella); guarda copia para deshacer | Solo datasets **propios**; el id se verifica contra el listado del contexto antes de borrar |
| `memory_history` / `memory_undo` | Ver versiones anteriores de una nota / deshacer la última corrección o restaurar una borrada | Solo datasets **propios**; historial en `ledger.sqlite` del gateway |
| `skills_list` / `skills_get` | Skills visibles para el contexto (carpeta propia + `shared` + `hub-share`) | Sin traversal ni symlinks |
| `secrets_list` | Nombres y hosts permitidos, **nunca valores** | – |
| `secret_http_request` | Hace el request HTTPS inyectando el secreto; la respuesta vuelve redactada | Host exacto de la policy, puerto 443, sin IPs privadas, sin redirects |

No hay tools para borrar secretos ni ejecutar comandos. Borrar/corregir memoria: solo notas del propio contexto
(`memory_delete` / `memory_update`, anotados como destructivos para que el cliente pida confirmación). **Regla de
`shared`:** ningún conector la borra ni la edita (los usuarios `ctx-*` solo tienen read+write ahí, sin delete); se corrige
como `hub-admin` en el host con `scripts/memory_admin.py list shared` / `delete shared <id> --yes`.
La auditoría registra tool, id y dataset, nunca el texto.

## Quickstart

**Producción:** seguí [`SETUP.es.md`](SETUP.es.md). Resumen:

```bash
./scripts/bootstrap.sh                          # genera .env / cognee.env y la deploy key
# completá TS_*, GH_OAUTH_* y el LLM de cognee.env
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
python3 scripts/bootstrap_infisical.py --bootstrap
python3 scripts/bootstrap_cognee.py
docker compose up -d
python3 scripts/smoke_test.py --quick
```

**Pruebas locales sin cuentas ni keys** (Docker, sin Tailscale ni OAuth, LLM falso):

```bash
docker compose -f compose.dev.yaml up -d --build
python3 scripts/bootstrap_cognee.py --url http://127.0.0.1:18000 --dev \
    --datasets-out dev/state/cognee-datasets.json --dev-keys-dir dev/state
docker compose -f compose.dev.yaml up -d --force-recreate gateway-dev-work gateway-dev-personal gateway-dev-side
python3 scripts/smoke_test.py --dev             # aislamiento entre contextos, skills, secretos
docker compose -f compose.dev.yaml down -v
```

Gateways dev: `http://127.0.0.1:18101/mcp` (work), `:18102` (personal), `:18103` (side).
El modo sin auth se niega a arrancar si `HUB_PUBLIC_URL` no es localhost.

**Unit tests:**

```bash
cd gateway && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest -q
```

## Actualizar (`scripts/update.sh`)

```bash
scripts/update.sh --dry-run        # qué cambiaría
scripts/update.sh                  # último tag v* (o un ref: scripts/update.sh v0.2.0)
```

fetch → checkout del ref → `scripts/render_compose.py` (si `.env` tiene `COMPOSE_FILE=compose.generated.yaml`) →
`pip install` del gateway en `.venv` → `docker compose build` → `up -d` → `scripts/smoke_test.py --quick` (3 intentos).
Si algo falla vuelve solo al ref anterior (rebuild + up + smoke) y sale con 1. Nunca corre `down` ni toca volúmenes
ni la config gitignored (`.env`, `cognee.env`, `config/*.yaml`, `secrets/`, `data/`). Historial en
`dev/state/updates.log`. Volver a mano: `scripts/update.sh <ref-anterior>`.

## Ver el grafo completo (solo vos, solo en el host)

`scripts/visualize.py` entra a Cognee como `hub-admin` (por `127.0.0.1:8010`, nada del tailnet ni de los gateways),
arma la vista combinada de todos los datasets (`POST /api/v1/visualize/multi`, un color por contexto) y abre el HTML.

```bash
python3 scripts/visualize.py --setup            # una sola vez: read + superuser para hub-admin (ctx-* no cambian)
python3 scripts/visualize.py                    # todo → ~/mnemos-graphs/global-AAAA-MM-DD.html
python3 scripts/visualize.py --context side     # un contexto (work | personal | side | shared); --with-shared suma shared
```

El HTML queda con `chmod 600` (tiene el contenido de tu memoria) y carga d3 desde `d3js.org`. No se agrega ninguna ruta
nueva: los gateways siguen sin poder ver otros contextos. Para deshacer el superuser:
`python3 scripts/visualize.py --revoke-superuser`.

Si `python3` es el del sistema (sin `httpx`), el script se re-ejecuta solo con `.venv/bin/python3` del repo.

## Dashboard en vivo (solo vos, solo en el host)

```bash
scripts/dashboard.sh open        # arranca (si hace falta) y abre http://127.0.0.1:8787
scripts/dashboard.sh status | stop | restart | logs
scripts/dashboard.sh install-agent     # arranque automático al iniciar sesión (LaunchAgent, KeepAlive)
scripts/dashboard.sh uninstall-agent   # vuelve al modo manual
scripts/dashboard.sh tailnet-on        # además en https://<mac>.<tailnet>.ts.net:8444 (solo tailnet, nunca Funnel)
scripts/dashboard.sh tailnet-off
```

Con el LaunchAgent (`io.mnemos.dashboard`) instalado, launchd lo arranca en cada login y lo relanza si el
proceso muere (a los ~10 s). Los mismos comandos siguen valiendo: `start`/`restart`/`status` pasan por `launchctl`, y
`stop` lo descarga (no vuelve solo hasta el próximo `start` o el próximo login). Siempre `--host 127.0.0.1`.

Una página que se refresca cada 20 s con: diagrama de arquitectura con cada pieza en verde/amarillo/rojo, estado de los
contenedores, los tres hubs públicos (Funnel + `/mcp` exigiendo OAuth), secretos por contexto (conteo en Infisical con la
identity *Viewer* de cada gateway y lo que la policy deja inyectar), datasets de Cognee con ítems/nodos, Ollama, skills,
último backup restic y los últimos commits. **Solo nombres, estados y conteos**: las credenciales que usa se leen de
`.env` del lado del servidor, nunca llegan al navegador, y cada respuesta se revisa contra los valores de `.env`.
Escucha solo en `127.0.0.1` (se niega a `0.0.0.0`), valida el header `Host` y no carga nada externo.
Puerto: `AIHUB_DASHBOARD_PORT` (default 8787). Log: `~/Library/Logs/mnemos-dashboard.log`.
**Tailnet (opcional):** `tailnet-on` hace `tailscale serve --bg --https=8444 http://127.0.0.1:8787` (443 es
Vaultwarden y 8443 Infisical; cambiá con `AIHUB_DASHBOARD_TS_PORT`) y verifica que quede *tailnet only*; si no, lo saca.
El server sigue escuchando solo en `127.0.0.1` y acepta además el `Host` `<nombre-magicdns>:8444` (lo lee de
`tailscale status --json` al arrancar). **Ojo:** la pestaña Explorar muestra texto de la memoria y nombres de secretos a
cualquier dispositivo de tu tailnet (los sidecars `tag:hub-public` no llegan si la policy no les da accesos salientes).

### Pestaña Explorar (`#explorar/<contexto>`)

Selector arriba: **Work / Personal / Side / Todos**. Para el contexto elegido muestra:

- **Grafo de memoria** interactivo (Cytoscape.js vendorizado, nada de CDN): un color por dataset, búsqueda, filtro
  "solo conceptos", y en dorado las entidades que aparecen en más de un contexto (o en más de un dataset dentro del
  contexto, p. ej. `side_blog` y `side_mnemos`), con el filtro "solo lo que tienen en común". Click en un nodo → sus vecinos.
- **Memorias guardadas** con fecha, app de origen (lo que el cliente MCP declara al conectarse; las guardadas antes de
  este cambio dicen "sin registro") y si ya están en el grafo o pendientes.
- **Skills visibles** para ese contexto (compartidas + propias, mismas reglas que el gateway); click → contenido.
- **Secretos**: solo nombre, dominios, métodos y header permitidos por la policy. Nunca valores.

Aislamiento: cada contexto se consulta con **su propia API key de Cognee** (la misma del gateway, así que ve solo lo que
ese gateway puede ver: sus datasets + `shared`). Solo **Todos** entra como `hub-admin`. Las credenciales quedan en el
servidor; las respuestas pasan por el mismo chequeo contra los valores de `.env`.

## Grafo vacío / memorias pendientes (`scripts/cognify_pending.py`)

Cada `memory_save` hace `add` (siempre funciona) y dispara `cognify` en background (extracción con el LLM). Con Ollama
+ `llama3.1:8b` y el framework por defecto de Cognee (`litellm_native`), el pedido va en "modo JSON" sin schema; el 8b a
veces omite campos o devuelve listas donde van strings, la validación falla tres veces y **el cognify de todo el dataset
se cae** (quedan 0 nodos, cada intento tarda ~15 min). Arreglo: `STRUCTURED_OUTPUT_FRAMEWORK=instructor` +
`LLM_ENDPOINT=…:11434/v1` + `OLLAMA_NUM_CTX=8192` en `cognee.env` (Ollama aplica el schema como gramática).

Para procesar lo que quedó pendiente (incremental, no borra nada; usa la key de cada contexto):

```bash
python3 scripts/cognify_pending.py --dry-run   # qué datasets tienen ítems sin procesar
python3 scripts/cognify_pending.py             # dispara cognify donde falta y espera; imprime nodos/aristas
```

El dashboard muestra "N pendientes" por dataset para verlo de un vistazo.

**Todas las noches a las 03:47** (30 min después del backup de las 03:17, que para Cognee) lo corre el LaunchAgent
`io.mnemos.cognify` vía `scripts/cognify_nightly.sh`: espera a que no haya un `backup.sh` corriendo y a que
Cognee responda, y después corre `cognify_pending.py` para todos los contextos. Instalarlo: `scripts/install_cognify_agent.sh
[--run-now]`. Log: `~/Library/Logs/mnemos-cognify.log`.

**Higiene de memoria.** `scripts/memory_hygiene.py` lee todas las notas de todos los datasets como hub-admin (solo
lectura) y escribe `~/mnemos-hygiene/hygiene-AAAA-MM-DD.md` (+ `.json`, chmod 600, se conservan 30): duplicados
exactos, casi duplicados, series de notas superadas (mismo PR/tarea), notas relacionadas, notas sin tags/app de origen
y textos con forma de secreto (solo el id). También consolida ("dream", sin LLM): agrupa por entidad las notas
que enlazan el mismo `[[Nombre]]`, propone el texto fusionado de cada casi duplicado del mismo dataset (la más nueva +
las frases de la vieja que falten) y lista notas posiblemente viejas (`--stale-days`, default 180). `--llm llama3.1:8b` agrega pistas de Ollama por par (orientativas).
`--apply` borra **solo** duplicados exactos del mismo dataset (conserva la de más tags; usa el usuario dueño); todo lo
demás queda como propuesta para decidir a mano con `memory_update` / `memory_delete` (o `memory_admin.py` en shared).
`cognify_nightly.sh` lo corre al terminar el cognify según `AIHUB_HYGIENE` = `report` (default) | `llm` | `apply` |
`off` (variable del plist).


## Watchdog Funnel/DNS (`scripts/hub_watchdog.py`)

Cada **15 min** el LaunchAgent `io.mnemos.watchdog` resuelve por DNS público (DoH a Cloudflare/Google =
1.1.1.1 / 8.8.8.8) los tres `hub-<ctx>.<tailnet>.ts.net` y hace POST a `/mcp` esperando **401**. Si el registro
público desaparece (MagicDNS local seguiría resolviendo) o la conexión falla, recrea **solo** ese
`ts-<ctx>` + `gateway-<ctx>` (`docker compose up -d --force-recreate …`), con **cooldown de 30 min** por hub.
También chequea Cognee en `127.0.0.1:8010` y reinicia el LaunchAgent del dashboard si `:8787` no responde.
**Nunca** ejecuta `tailscale funnel` ni publica nada nuevo.

```bash
./scripts/install_watchdog_agent.sh --run-now
tail -f ~/Library/Logs/mnemos-watchdog.log
cat ~/Library/Logs/mnemos-watchdog-status.json   # lo muestra el dashboard
./scripts/hub_watchdog.py --dry-run            # chequear sin recrear
```

Desinstalar: `./scripts/install_watchdog_agent.sh --uninstall`
(o `launchctl bootout gui/$(id -u)/io.mnemos.watchdog && rm ~/Library/LaunchAgents/io.mnemos.watchdog.plist`).

Estado y cooldown: `~/Library/Application Support/mnemos/watchdog-state.json`. El dashboard tiene una tarjeta
"Watchdog Funnel/DNS" con última corrida, DNS/MCP por hub y último recreate.

### Alertas fuera de la Mac (opcional)

Apagadas por defecto. Para activarlas, agregá en `.env` una o las dos:

- `MNEMOS_ALERT_WEBHOOK_URL=`: incoming webhook de Slack o Discord (POST JSON con `text` y `content`).
- `MNEMOS_ALERT_NTFY_TOPIC=`: topic de [ntfy](https://ntfy.sh). Suscribite desde la app del celular. Usá un topic
  largo y aleatorio, porque quien lo conozca puede leerlo.

Después probá con `./scripts/hub_watchdog.py --test-alert`. No hace falta reinstalar el LaunchAgent: lee `.env` en cada
corrida. Llega 1 alerta cuando un componente cae (`hub-<ctx>`, `cognee`, `dashboard`) y 1 cuando se recupera. Si
flapea dentro de `MNEMOS_ALERT_COOLDOWN_S` (default 30 min), no se repite. El mensaje no incluye hostnames, tailnet ni
IPs. No alerta en `--dry-run`, en `--no-notify` ni cuando la Mac está sin red.

## Seguridad (lo mínimo que hay que saber)

- Nunca se commitean `.env`, `cognee.env`, `secrets/`, `data/` ni `backups/` (están en `.gitignore`).
- Lo único público son los tres `hub-<ctx>`, todos detrás de OAuth con allowlist (`HUB_ALLOWED_GITHUB_LOGINS`).
- Cada gateway tiene su propia API key de Cognee y su propia identidad de Infisical: aunque un gateway tuviera un
  bug, Cognee e Infisical le niegan (403) los datos de otro contexto. Esto está verificado en las pruebas.
- Auditoría en `/data/audit.log` (JSONL) de cada gateway: qué tool, contexto y resultado; nunca contenidos ni secretos.
- Backups: `scripts/backup.sh` (restic, diario 03:17 vía `scripts/install_backup_agent.sh`) y `scripts/restore.sh --check`.
- Offsite: al terminar, `backup.sh` corre `scripts/offsite_sync.sh` → `rclone sync` del repo restic (ya cifrado) a
  `gdrive:mnemos-backups` (Google Drive, scope `drive.file`: rclone sólo ve lo que él crea) + `rclone check` (MD5).
  Log `~/Library/Logs/mnemos-offsite.log`; el dashboard muestra la última copia. El token OAuth vive en
  `~/.config/rclone/rclone.conf` (fuera del repo; anotarlo en Vaultwarden). Restore desde Drive sin la Mac:
  `restic -r rclone:gdrive:mnemos-backups snapshots` (o bajar la carpeta y usarla como repo local).

## Exportar / importar memoria en Markdown (compatible con gbrain)

`scripts/memory_markdown.py` (en el host, como hub-admin) pasa la memoria a una carpeta de archivos `.md` con el
formato de páginas de gbrain (frontmatter `type`, `title`, `date`, `tags` + un bloque `mnemos:` con id, dataset y app
de origen) y la vuelve a cargar:

```bash
.venv/bin/python3 scripts/memory_markdown.py export ~/mnemos-export                 # todos los datasets
.venv/bin/python3 scripts/memory_markdown.py export ~/mnemos-export --dataset personal
.venv/bin/python3 scripts/memory_markdown.py import ~/gbrain-export --dataset personal   # muestra el plan
.venv/bin/python3 scripts/memory_markdown.py import ~/gbrain-export --dataset personal --yes
```

- La carpeta exportada se puede importar en gbrain (`gbrain import <dir>`) y una exportación de gbrain
  (`gbrain export --dir`) se puede importar acá. La sección de timeline de gbrain se conserva como texto.
- El import va a UN dataset, saltea textos que ya están y archivos ocultos, y sin `--yes` no guarda nada.
- El export queda con tu memoria en texto plano: guardalo como cualquier backup privado.
