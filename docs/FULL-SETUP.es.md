# Setup completo (como el del autor)

> English version: [`FULL-SETUP.md`](FULL-SETUP.md).

Este es Mnemos self-hosted **entero**, como corre la instancia de referencia: el hub en Docker con Cognee y Ollama,
tres contextos (`work` / `personal` / `side`), secretos en Infisical y Vaultwarden, sidecars de Tailscale, el
**conector único** (`https://mnemos.<tailnet>.ts.net/mcp`) con login propio + passkeys (Google opcional), el
dashboard, el watchdog con alertas al celular, cognify nocturno + higiene con LLM, backups y `scripts/update.sh`.

¿Solo querés memoria en una Mac, sin Docker? Usá la [app Desktop](INSTALL-mac.md).

Esta página es el **mapa**: da el orden y las decisiones, y enlaza a los pasos detallados de
[`SETUP.es.md`](SETUP.es.md) (abajo, `SETUP §N`), [`router.md`](router.md), [`auth-local.md`](auth-local.md),
[`google-login.md`](google-login.md), [`CLIENTS.md`](CLIENTS.md) y [`OPERATIONS.es.md`](OPERATIONS.es.md) (algunos
solo en inglés). La primera vez lleva entre 1,5 y 3 horas, casi todo en consolas web (Tailscale, Infisical, las apps de IA).

Marcadores: `<tailnet>` = tu nombre de tailnet (lo de antes de `.ts.net`), `<host>` = el nombre MagicDNS de la
máquina, `<vos>` = el nombre de usuario que elijas para Mnemos.

## 1. Qué tenés al final

```
Claude / ChatGPT / Grok / Cursor
        │  MCP por HTTPS + OAuth (login propio, passkey, Google opcional)
        ▼
ts-mnemos (Funnel) → hub-router ──red interna de Docker──► gateway-work / gateway-personal / gateway-side
                                                               │            │             │
                                                     Cognee (grafo + vectores)   Infisical (secretos)   skills
Solo tailnet (nunca público): Vaultwarden :443, Infisical :8443, dashboard :8444 (opcional)
Host: Ollama (llama3.1:8b + paraphrase-multilingual), dashboard 127.0.0.1:8790, jobs de launchd/systemd
```

Las URLs por contexto (`https://hub-<ctx>.<tailnet>.ts.net/mcp`) siguen andando junto al router; podés usar
cualquiera de las dos o ambas.

## 2. Antes de empezar

### Máquina

- **Host siempre prendido**: una Mac (referencia) o un Linux. No se tiene que dormir enchufado, o se frenan el hub,
  los jobs nocturnos y los backups (macOS: `sudo pmset -c sleep 0 disksleep 0`, ver SETUP §14).
- **RAM**: 16 GB recomendados. Docker necesita 8 GB+ (Cognee ~2 GB, Infisical ~1,3 GB, el resto < 0,5 GB) y Ollama
  ~5–6 GB más con `llama3.1:8b` cargado. Con 8 GB en total, usá una key de OpenAI en vez de Ollama.
- **Disco**: ~15 GB libres (imágenes de Docker ~5 GB, modelos de Ollama ~5,5 GB; datos y backups locales crecen).

### Cuentas y apps

| Qué | ¿Hace falta? | Para qué |
|---|---|---|
| Docker Desktop (macOS) / Docker Engine + Compose v2 (Linux) | sí | corre todo el stack |
| git, Python 3.11+ | sí | clonar, scripts de bootstrap, dashboard, watchdog, tests |
| Cuenta de Tailscale + app en el host (y en el celular) | sí | red privada para las UIs de admin; Funnel es la única entrada pública |
| Ollama (<https://ollama.com>) **o** una API key de OpenAI | uno de los dos | extracción del grafo de Cognee, `memory_answer`, higiene con LLM, búsqueda semántica local (Ollama) |
| Cuentas de Claude / ChatGPT / Grok / Cursor | las que uses | los asistentes que comparten la memoria (límites de plan en §8) |
| App ntfy (iOS/Android) o un webhook de Slack/Discord | opcional | alertas del watchdog en el celular |
| restic + un destino de backup (disco externo, B2, S3, …) | recomendado | backups nocturnos cifrados |
| rclone + un remote en la nube (p. ej. Google Drive) | opcional | copia offsite del repo restic |
| Proyecto de Google Cloud (gratis, sin facturación) | opcional | "Sign in with Google" en el router |
| Cuenta de GitHub | opcional | solo para login con GitHub (OAuth apps) o un repo privado de skills |
| CLI `gh` | opcional | comodidad |

Vaultwarden e Infisical corren dentro de Docker: sus cuentas admin las creás vos, sin registrarte en ningún lado.
El plan gratis de Infisical permite 5 machine identities (3 contextos usan 3).

### Decisiones (tomalas ahora; cambiar algunas después implica migrar)

| Decisión | Esta guía (setup de referencia) | Alternativa |
|---|---|---|
| Login de los asistentes | **propio** (`HUB_AUTH_PROVIDER=local`) + passkeys, Google opcional | OAuth de GitHub: una OAuth App por contexto (SETUP §6) |
| Conectores | **conector único** (router) | un conector por contexto |
| LLM de extracción | **Ollama** `llama3.1:8b` (gratis, local, más lento) | OpenAI `gpt-4o-mini` (pago, mejor) |
| Embeddings (Cognee) | locales `paraphrase-multilingual-MiniLM-L12-v2` (fastembed) | OpenAI; **se fijan el día uno** (cambiarlos = reindexar) |
| Skills | carpeta local `./skills-local` (se editan en el dashboard) | repo privado de GitHub + deploy key |
| Contextos | `work`, `personal`, `side` | los tuyos (`scripts/mnemos context add`) |

Con el login propio **no** necesitás cuenta ni OAuth App de GitHub. Las líneas `GITHUB_USER` y `GH_OAUTH_*` quedan en
`__COMPLETAR__` en `.env` y se ignoran, siempre que pongas `HUB_ALLOWED_GITHUB_LOGINS=<vos>` (§4).

## 3. Instalar las apps del host

1. **Docker**: SETUP §2 (arranque al iniciar sesión, 8 GB+ de RAM). Verificá `docker info` y `docker compose version`.
2. **Tailscale**: SETUP §3: MagicDNS + certificados HTTPS activados, pegá `config/tailscale/policy-snippet.hujson` en
   *Access controls* (cubre todos los sidecars `hub-*` **y** el sidecar `mnemos` del router: todos usan
   `tag:hub-public`), creá una auth key reusable, preaprobada y con el tag `tag:hub-public`.
3. **Ollama** (salteá si usás OpenAI):

   ```bash
   brew install ollama          # o la app de ollama.com; Linux: curl -fsSL https://ollama.com/install.sh | sh
   ollama pull llama3.1:8b      # ~4,9 GB: extracción de Cognee, memory_answer, higiene con LLM
   ollama pull paraphrase-multilingual   # ~0,6 GB: embeddings de la búsqueda semántica local de los gateways
   ollama list                  # tienen que aparecer los dos
   ```

   | Modelo | Lo usa | Configuración |
   |---|---|---|
   | `llama3.1:8b` | extracción del grafo de Cognee | `LLM_MODEL` en `cognee.env` (opción B) |
   | `llama3.1:8b` | `memory_answer` (respuestas con citas) | `HUB_ANSWER_MODEL` (+ `HUB_LLM_URL`) en `.env` |
   | `llama3.1:8b` | higiene nocturna con LLM | `MNEMOS_HYGIENE=llm` (+ `MNEMOS_HYGIENE_MODEL`) en `.env` |
   | `paraphrase-multilingual` | `memory_search` modo `auto`/`hybrid` | `HUB_EMBED_URL` + `HUB_EMBED_MODEL` en `.env` |

   Los embeddings propios de Cognee **no** son un modelo de Ollama: corren dentro del contenedor de Cognee (fastembed).

   Los contenedores tienen que llegar a Ollama en el host:
   - macOS: `http://host.docker.internal:11434`. Si `docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags`
     no responde, Ollama escucha solo en loopback: `launchctl setenv OLLAMA_HOST 0.0.0.0`, reiniciá Ollama y bloqueá
     el puerto 11434 desde la LAN con el firewall de macOS.
   - Linux: agregá `extra_hosts: ["host.docker.internal:host-gateway"]` al servicio `cognee` (si usás
     `compose.generated.yaml`, editá `compose.yaml`, que es su plantilla). Los gateways comparten la red de su sidecar
     `ts-<ctx>` y no aceptan `extra_hosts`: apuntá `HUB_EMBED_URL` a la IP del bridge de Docker del host
     (`ip -4 addr show docker0`, normalmente `http://172.17.0.1:11434`). Hacé que Ollama escuche ahí
     (`OLLAMA_HOST=0.0.0.0` en su unidad de systemd) y bloqueá el puerto 11434 desde la LAN.
     Más simple en un servidor: Ollama como contenedor en la red de compose, sin puerto en el host
     ([ollama-linux.md](ollama-linux.md), en inglés; cubre también iGPUs de AMD y los topes de contexto y salida).

## 4. Preparar la configuración

```bash
git clone https://github.com/ngentile92/mnemos && cd mnemos
./scripts/bootstrap.sh                 # .env + cognee.env (chmod 600), config/*.yaml, claves generadas
python3 -m venv .venv && .venv/bin/pip install -e "gateway[dev]"
.venv/bin/python -m hub_gateway.local_auth hash     # elegí una contraseña de 12+ caracteres; imprime scrypt:...
```

Completá `.env` (detalle en SETUP §4). Para este setup:

| Variable | Valor |
|---|---|
| `TS_TAILNET`, `NOTEBOOK_TS_NAME`, `TS_AUTHKEY_PUBLIC` | de Tailscale (§3) |
| `HUB_AUTH_PROVIDER` | `local` |
| `HUB_LOCAL_USER` / `HUB_LOCAL_PASSWORD_HASH` | `<vos>` / la línea `scrypt:...` |
| `HUB_ALLOWED_GITHUB_LOGINS` | `<vos>` (agregá la línea: los hubs solo aceptan los logins de esta lista, que por defecto toma `GITHUB_USER`, todavía en `__COMPLETAR__`) |
| `HUB_PASSKEYS` | `1` |
| `COMPOSE_PROFILES` | `router` |
| `HUB_EMBED_URL` / `HUB_EMBED_MODEL` | `http://host.docker.internal:11434` / `paraphrase-multilingual` |
| `HUB_ANSWER_MODEL` | `llama3.1:8b` (`HUB_LLM_URL` toma `HUB_EMBED_URL` por defecto) |
| `MNEMOS_HYGIENE` | `llm` |
| `RESTIC_REPOSITORY` / `RESTIC_PASSWORD` | tu destino de backup / una contraseña larga (también en papel) |
| `MNEMOS_ALERT_NTFY_TOPIC` | un topic largo y aleatorio (§9) |

Después `cognee.env`: comentá la opción A y descomentá la B (Ollama) tal como está escrita (SETUP §7: `instructor`,
endpoint **con** `/v1`, `OLLAMA_NUM_CTX=8192`). No toques las líneas de embeddings.

Por último `config/contexts.yaml`: tu nombre (`owner`) y qué va en cada contexto. Corré `./scripts/bootstrap.sh` otra
vez después de poner `TS_TAILNET` / `NOTEBOOK_TS_NAME` (arma `VW_DOMAIN` e `INFISICAL_SITE_URL`). Ignorá
`GITHUB_USER` y `GH_OAUTH_*` en su lista de "faltan completar".

## 5. Levantar la capa privada

```bash
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
tailscale serve --bg --https=443  http://127.0.0.1:8081   # Vaultwarden, solo tailnet
tailscale serve --bg --https=8443 http://127.0.0.1:8082   # Infisical, solo tailnet
```

(Todavía no `scripts/mnemos expose --apply`: también levantaría los gateways, que necesitan SETUP §10–11 antes.
Nunca `tailscale funnel` en el host.)

Después SETUP §9 (cuenta de Vaultwarden, luego `VW_SIGNUPS_ALLOWED=false`; guardá `.env` y `cognee.env` como nota
segura), SETUP §10 (`bootstrap_infisical.py --bootstrap`, después **borrá la Instance Admin Identity**; cargá tus
secretos de APIs y listalos en `config/secret-policy.yaml`) y SETUP §11 (`bootstrap_cognee.py`).

## 6. Encender la capa pública

```bash
docker compose up -d                   # gateways, sidecars hub-*, router + ts-mnemos (COMPOSE_PROFILES=router)
.venv/bin/python scripts/smoke_test.py --quick
scripts/mnemos expose --check          # cada hub-<ctx> y mnemos: ok (DNS público + /mcp → 401)
```

Desde el celular con datos móviles y **sin** Tailscale: `https://mnemos.<tailnet>.ts.net/mcp` responde 401 y
`https://<host>.<tailnet>.ts.net` (Vaultwarden) no responde.

## 7. Conector único, passkeys y Google

- Detalle del router y de la pantalla de consentimiento: [`router.md`](router.md).
- **Passkey**: abrí `https://mnemos.<tailnet>.ts.net/passkey/enroll` en cada dispositivo donde la quieras (cada host
  necesita la suya: el router y cada `hub-<ctx>` que uses directo) — [`auth-local.md`](auth-local.md#passkeys-optional).
- **Google** (opcional): [`google-login.md`](google-login.md), después `scripts/update.sh`.

## 8. Conectar los asistentes

Agregá **un** conector `https://mnemos.<tailnet>.ts.net/mcp`, iniciá sesión y tildá los contextos que puede usar esa
app. Pasos por cliente: [`CLIENTS.md`](CLIENTS.md). Límites de plan, primero:

- **Claude**: Free permite un conector custom (alcanza con el router); uno por contexto requiere Pro/Max.
- **ChatGPT**: developer mode, solo web. Lectura **y** escritura solo en Business/Enterprise/Edu; **Pro es solo
  lectura** (`memory_save` bloqueado); Plus no figura.
- **Grok**: grok.com/connectors → Custom. Business/Enterprise necesitan que un admin lo habilite antes.
- **Cursor**: `~/.cursor/mcp.json` con la URL, sin secretos.

Verificá en cada uno: `hub_whoami`, guardá un hecho en un cliente y buscalo desde otro. La tarjeta **Connected apps**
del dashboard lista cada app y permite revocarla.

## 9. Que siga andando

```bash
./scripts/dashboard.sh install-agent            # http://127.0.0.1:8790 (scripts/mnemos expose --dashboard: tailnet :8444)
./scripts/install_watchdog_agent.sh --run-now   # cada 15 min: Funnel/DNS + /mcp; recrea un sidecar caído
./scripts/install_cognify_agent.sh              # 03:47: cognify de memorias pendientes + higiene (MNEMOS_HYGIENE)
./scripts/install_backup_agent.sh --run-now     # 03:17: backup con restic (+ copia offsite si configuraste rclone)
```

En Linux los mismos scripts instalan unidades de usuario de systemd (o corré `scripts/install_systemd_units.sh` y
después `sudo loginctl enable-linger "$USER"`); ver [SETUP → Linux](SETUP.md#linux-systemd).

- **Alertas al celular (ntfy)**: instalá la app ntfy, *Subscribe to topic* → el mismo topic largo y aleatorio que
  `MNEMOS_ALERT_NTFY_TOPIC` (servidor `https://ntfy.sh` salvo que pongas `MNEMOS_ALERT_NTFY_SERVER`). Después corré
  `./scripts/hub_watchdog.py --test-alert`: te tiene que llegar la notificación. Cualquiera que sepa el topic lo puede leer.
- **Higiene**: `MNEMOS_HYGIENE=report|llm|apply|off` (default `report`). `llm` suma contradicciones y notas vencidas
  con `MNEMOS_HYGIENE_MODEL` (default `llama3.1:8b`). Los reportes van a `~/mnemos-hygiene/`; no se edita nada salvo
  duplicados exactos con `apply`. Para correrla a mano: `scripts/mnemos hygiene --llm llama3.1:8b`.
- **Backups**: `brew install restic`, después `./scripts/backup.sh` y `./scripts/restore.sh --check` (SETUP §14).
- **Copia offsite** (opcional): `brew install rclone`, `rclone config create gdrive drive scope=drive.file`
  (OAuth en el navegador; rclone solo ve lo que él crea), opcionalmente `MNEMOS_OFFSITE_REMOTE=gdrive:mnemos-backups`
  en `.env`, y `scripts/offsite_sync.sh` una vez. `backup.sh` la corre al final de cada backup. Requiere que
  `RESTIC_REPOSITORY` sea una carpeta local.
- **Que el host no se duerma**: `sudo pmset -c sleep 0 disksleep 0` (macOS, enchufado).

## 10. Actualizar y volver atrás

```bash
scripts/update.sh --dry-run     # qué cambiaría
scripts/update.sh               # último tag v*; re-renderiza compose, rebuild, smoke test y rollback automático si falla
```

Nunca `docker compose down -v`: los volúmenes son tu memoria.

## 11. Checklist final

- [ ] `scripts/mnemos expose --check` → todo ok; Vaultwarden/Infisical no responden sin Tailscale
- [ ] Cada asistente: `hub_whoami` muestra solo los contextos que tildaste; un hecho guardado en uno aparece en otro
- [ ] El login con passkey anda en el celular
- [ ] Dashboard en verde; Connected apps lista tus asistentes
- [ ] `./scripts/hub_watchdog.py --test-alert` llega al celular
- [ ] A la mañana siguiente: `~/Library/Logs/mnemos-cognify.log` (Linux: `~/.local/state/mnemos/`) muestra la corrida
      nocturna y hay un reporte de higiene en `~/mnemos-hygiene/`
- [ ] `restic snapshots --tag mnemos` lista un snapshot; una vez por mes, probá un restore completo en otro lado
