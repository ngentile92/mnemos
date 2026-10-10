# Mnemos — setup paso a paso (español)

> English version: [`SETUP.md`](SETUP.md).

Todo lo que se podía automatizar ya está en el repo (compose, gateway, scripts, tests). Lo que queda
necesita tus cuentas, tu máquina o tu criterio. Cada paso dice **qué valor exacto** poner y **cómo verificar**.
Está escrito para macOS con Docker Desktop (el host de referencia); en Linux los pasos son los mismos salvo
Docker Desktop, `host.docker.internal` (agregalo con `extra_hosts: ["host.docker.internal:host-gateway"]`) y los
LaunchAgents (usá cron o systemd).

Convenciones: `<tailnet>` = tu nombre de tailnet (lo de antes de `.ts.net`), `<mac>` = el nombre MagicDNS
del host en Tailscale, `<usuario>` = tu login de GitHub (`GITHUB_USER`). Requisitos: Docker + Compose v2,
Python 3.11+, git, `gh` (opcional), 8 GB de RAM libres para Docker; Ollama si querés extracción local gratis.

---

## 0. Base de seguridad (5 min)

1. FileVault prendido: `fdesetup status` → tiene que decir `FileVault is On.`
2. GitHub con passkey o 2FA (es el login de todos los conectores).

## 1. Tu repo privado de skills

Opcional: si usás la carpeta local (`SKILLS_DIR=./skills-local`, default en `.env.example`, editable desde el dashboard), salteá este paso y el 5.

Las skills viven en un repo **privado** aparte (`<usuario>/mnemos-skills`) que `skills-sync` clona en modo
solo lectura. Arrancalo con los ejemplos de este repo:

```bash
mkdir mnemos-skills && cp -R mnemos/examples/skills/. mnemos-skills/ && cd mnemos-skills
# una carpeta por contexto de config/contexts.yaml dentro de skills/, más shared/
git init && git add . && git commit -m "Skills iniciales"
gh repo create <usuario>/mnemos-skills --private --source . --remote origin --push
```

Formato y reglas de visibilidad: [`examples/skills/README.md`](../examples/skills/README.md).

## 2. Docker Desktop

1. Abrí Docker Desktop (el daemon no estaba corriendo). Settings → General → *Start Docker Desktop when you sign in*.
2. Settings → Resources → Memory: **8 GB o más** (medido en pruebas: Cognee ~2 GB, Infisical ~1,3 GB, el resto < 0,5 GB).
3. Verificá: `docker info` sin error y `docker compose version` (v2).

## 3. Tailscale

1. Instalá el cliente de macOS desde <https://tailscale.com/download> (variante Standalone recomendada) y logueate.
   Instalá también la app en el celular con la misma cuenta.
2. CLI: en la app Standalone, *Settings → CLI integration → Install Now* (deja `tailscale` en `/usr/local/bin`).
   En la variante App Store el binario es `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
3. Admin console → **DNS**: MagicDNS **on** y **HTTPS Certificates → Enable**. Anotá el nombre del tailnet
   (`<tailnet>.ts.net`) → `TS_TAILNET=<tailnet>` en `.env`. Anotá el nombre de la Mac → `NOTEBOOK_TS_NAME=<mac>`.
4. Admin console → **Access controls**: pegá/mezclá `config/tailscale/policy-snippet.hujson`
   (`tag:hub-public` con owner `autogroup:admin`, `nodeAttrs` con `funnel` solo para `tag:hub-public`, y ninguna
   regla con `src: tag:hub-public`).
5. Admin console → **Settings → Keys → Generate auth key**: *Reusable* ✔, *Ephemeral* ✘, *Pre-approved* ✔,
   *Tags* = `tag:hub-public`. Copiala a `TS_AUTHKEY_PUBLIC=` en `.env`.
6. Verificá: `tailscale status` muestra la Mac y el celular.

## 4. Preparar `.env` y `cognee.env`

```bash
cd mnemos
./scripts/bootstrap.sh          # genera claves, .env y cognee.env (chmod 600), config/*.yaml y la deploy key de skills
python3 -m venv .venv && . .venv/bin/activate && pip install -e "gateway[dev]"   # para scripts y tests
```

`bootstrap.sh` lista lo que falta completar (como mínimo `GITHUB_USER`, `TS_*` y `GH_OAUTH_*`). También crea
`config/contexts.yaml` y `config/secret-policy.yaml` desde los ejemplos (gitignoreados): editá el `owner` y las
descripciones de cada contexto. Volvé a correrlo después de cargar `TS_TAILNET` y
`NOTEBOOK_TS_NAME`: arma `VW_DOMAIN=https://<mac>.<tailnet>.ts.net` e `INFISICAL_SITE_URL=https://<mac>.<tailnet>.ts.net:8443`.

## 5. Deploy key del repo de skills

`bootstrap.sh` imprime `secrets/skills_deploy_key.pub`. En
`https://github.com/<usuario>/mnemos-skills/settings/keys` → *Add deploy key*: título `mnemos skills-sync`,
pegá la clave, **Allow write access: NO**.

## 6. Tres OAuth Apps de GitHub (una por contexto)

En <https://github.com/settings/developers> → *OAuth Apps* → *New OAuth App* (cuenta personal, no la org):

| Application name | Homepage URL | Authorization callback URL | Variables en `.env` |
|---|---|---|---|
| `mnemos-work` | `https://hub-work.<tailnet>.ts.net` | `https://hub-work.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_WORK_ID` / `_SECRET` |
| `mnemos-personal` | `https://hub-personal.<tailnet>.ts.net` | `https://hub-personal.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_PERSONAL_ID` / `_SECRET` |
| `mnemos-side` | `https://hub-side.<tailnet>.ts.net` | `https://hub-side.<tailnet>.ts.net/auth/callback` | `GH_OAUTH_SIDE_ID` / `_SECRET` |

*Enable Device Flow*: no. Después de crear cada una: *Generate a new client secret* y copialo (se ve una sola vez).
Opcional para probar el login antes de exponer nada: una cuarta app `mnemos-dev` con callback
`http://localhost:8000/auth/callback`.

## 7. Elegir el LLM de Cognee (en `cognee.env`)

Leer memoria **no** usa LLM (el gateway pide `only_context`). Escribir sí (extracción del grafo).

- **Opción A — API key** (mejor calidad): dejá `LLM_PROVIDER=openai`, `LLM_MODEL=openai/gpt-4o-mini` (o el modelo
  que prefieras) y poné `LLM_API_KEY=`. Costo proporcional a lo que guardes.
- **Opción B — Ollama en la Mac** (gratis, más lento): `ollama pull llama3.1:8b` (~4.9 GB) y en
  `cognee.env` comentá la A y descomentá la B con `LLM_MODEL=llama3.1:8b` (Cognee valida Llama 3.1/3.2 para la
  extracción; con `qwen2.5:7b` loguea un warning de *known limitations* y puede perder entidades),
  `STRUCTURED_OUTPUT_FRAMEWORK=instructor`, `LLM_ENDPOINT=http://host.docker.internal:11434/v1` (**con** `/v1`) y
  `OLLAMA_NUM_CTX=8192`. Sin `instructor`, Cognee pide "modo JSON" sin schema: el 8b a veces devuelve campos de menos,
  falla la validación y el cognify de todo el dataset queda en 0 nodos (ver README, *Grafo vacío*). Verificá que el contenedor llegue:
  `docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags`. Si no responde, Ollama está
  escuchando solo en loopback: `launchctl setenv OLLAMA_HOST 0.0.0.0` y reiniciá Ollama (eso lo expone a tu LAN;
  mejor restringilo con el firewall de macOS).
- Embeddings: por defecto **locales y multilingües** (`fastembed`, `paraphrase-multilingual-MiniLM-L12-v2`).
  No los cambies después de cargar datos (obliga a reindexar).
  Para cambiar de modelo después: editá solo `LLM_MODEL` en `cognee.env` y `docker compose up -d cognee`
  (lo ya extraído queda; los embeddings no cambian).

## 8. Levantar credenciales y memoria (sin nada público todavía)

```bash
docker compose up -d vaultwarden infisical-db infisical-redis infisical cognee skills-sync
docker compose ps        # todo "running"; Infisical tarda ~1 min la primera vez (migraciones)
tailscale serve --bg --https=443  http://127.0.0.1:8081   # Vaultwarden (solo tailnet)
tailscale serve --bg --https=8443 http://127.0.0.1:8082   # Infisical  (solo tailnet)
tailscale serve status
```

**Nunca** uses `tailscale funnel` en la Mac: lo único público son los sidecars `hub-*`.

## 9. Cuenta de Vaultwarden

1. Desde un dispositivo del tailnet abrí `https://<mac>.<tailnet>.ts.net` → *Create account* (master password larga, anotada en papel).
2. En `.env`: `VW_SIGNUPS_ALLOWED=false` y `docker compose up -d vaultwarden`. Verificá que *Create account* ya no funcione.
3. Guardá una **nota segura** con el contenido completo de `.env` y `cognee.env` (copia maestra).

## 10. Infisical: tu cuenta admin, proyectos e identidades

```bash
python3 scripts/bootstrap_infisical.py --bootstrap
```

Te pide email y contraseña: **esa es tu cuenta admin de Infisical**. El script crea la organización `mnemos`,
los proyectos `hub-work`, `hub-personal`, `hub-side`, `hub-shared` (entornos dev/staging/prod), las carpetas
`/shop`, `/blog`, `/mnemos` en `hub-side/prod`, y las identidades `mi-gw-work|personal|side`
(Universal Auth, rol **Viewer** en su proyecto y en `hub-shared`), y escribe `INF_MI_*` en `.env`.
Después: entrá a `https://<mac>.<tailnet>.ts.net:8443`, cargá tus secretos en `prod` y listalos (sin valores) en
`config/secret-policy.yaml`. Límite Free: 5 identidades.

**Borrá la *Instance Admin Identity* cuando termines el bootstrap.** Nada del hub la usa (gateways y dashboard usan
`mi-gw-*` con `INF_MI_*`; ni `.env` ni compose tienen credenciales de admin). El bootstrap le crea un token de Token
Auth con TTL de 90 días que solo vivió en memoria del script. En la UI (`https://<mac>.<tailnet>.ts.net:8443`, con tu
usuario admin): **Organization Settings → Access Control → Identities → Instance Admin Identity → ⋮ → Delete** (si
tiene *delete protection*, primero desactivala en la misma página). Borrar la identity revoca su token. Para
importar secretos después (`import_env_secrets.py --apply`, que necesita escritura) creá una identity temporal con rol
Member en los proyectos destino y borrala al terminar, o cargalos a mano en la UI.

## 11. Cognee: usuarios, datasets y API keys

```bash
python3 scripts/bootstrap_cognee.py
```

Lee `config/contexts.yaml` y crea `hub-admin`, un usuario `ctx-<contexto>` por contexto, un dataset por contexto (o
por proyecto: con el ejemplo, `work`, `personal`, `side_shop`, `side_blog`, `side_mnemos`) y `shared` (read+write para
todos los contextos), escribe `config/cognee-datasets.json` (gitignoreado) y las `COGNEE_KEY_*` en `.env`. Es idempotente.

## 12. Encender la capa pública y probar

```bash
docker compose up -d                       # suma los 3 sidecars de Funnel y los 3 gateways
python3 scripts/smoke_test.py --quick      # Cognee OK + 401 y metadata OAuth en los 3 hostnames
```

Desde datos móviles **sin** Tailscale: `https://hub-personal.<tailnet>.ts.net/mcp` responde 401;
`https://<mac>.<tailnet>.ts.net` (Vaultwarden) **no** responde. En el admin de Tailscale aparecen
`hub-work`, `hub-personal`, `hub-side` con el tag `tag:hub-public`.

## 13. Conectar los asistentes (un conector por contexto)

Pasos detallados y qué está verificado: [`CLIENTS.md`](CLIENTS.md) (en inglés).

URLs: `https://hub-work.<tailnet>.ts.net/mcp`, `https://hub-personal.<tailnet>.ts.net/mcp`,
`https://hub-side.<tailnet>.ts.net/mcp`. En todos, el login es con **tu** GitHub; otra cuenta es rechazada.

- **Claude** (web; después aparece en Desktop y móvil): *Customize → Connectors → Add custom connector*. Nombre
  `Hub · Personal` (y los otros dos), URL de arriba, auth OAuth (dejá que registre el cliente automáticamente).
  Tres conectores custom requieren plan Pro o Max. En cada chat activá solo el del contexto.
- **ChatGPT** (solo web): *Settings → Security and login → Developer mode* on. En chatgpt.com, crear una app de
  developer mode por contexto con la URL, transporte streamable HTTP y auth **OAuth**. Probá `memory_save`:
  según tu plan puede quedar solo lectura.
- **Grok**: grok.com/connectors → *New Connector* → *Custom* → URL y autenticación. Si el OAuth no completa, probá un puente MCP
  local (p. ej. MCP SuperAssistant).
- **Cursor**: en `~/.cursor/mcp.json` → `{"mcpServers": {"hub-side": {"url": "https://hub-side.<tailnet>.ts.net/mcp"}}}`
  (sin secretos; Cursor hace el OAuth).

Verificá en cada uno: `hub_whoami` devuelve el contexto correcto; guardá un hecho en `personal` desde Claude y
buscalo desde otro cliente; desde el conector `work` no aparece.

## 14. Backups

```bash
brew install restic
# en .env: RESTIC_REPOSITORY (disco externo o nube) y RESTIC_PASSWORD (también en Vaultwarden y en papel)
./scripts/backup.sh
./scripts/restore.sh --check
```

Programalo diario con el LaunchAgent del repo (macOS; en Linux: `17 3 * * * cd /ruta/a/mnemos && ./scripts/backup.sh` en cron) (corre `backup.sh` todos los días a las **03:17** hora local;
si la Mac estaba dormida, launchd lo corre apenas se despierta):

```bash
./scripts/install_backup_agent.sh --run-now   # instala ~/Library/LaunchAgents/io.mnemos.backup.plist y corre uno ya
tail -f ~/Library/Logs/mnemos-backup.log        # log de cada corrida
restic snapshots --tag mnemos                  # con RESTIC_REPOSITORY/RESTIC_PASSWORD exportadas desde .env
```

La retención la aplica el mismo `backup.sh` al final: `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`.
Para desinstalarlo: `launchctl bootout gui/$(id -u)/io.mnemos.backup && rm ~/Library/LaunchAgents/io.mnemos.backup.plist`.

Dos LaunchAgents más, opcionales (solo macOS; prefijo `MNEMOS_LABEL_PREFIX`, default `io.mnemos`):

```bash
./scripts/install_cognify_agent.sh      # 03:47 diario: procesa al grafo las memorias pendientes (log ~/Library/Logs/mnemos-cognify.log)
./scripts/dashboard.sh install-agent    # dashboard en 127.0.0.1:8787 al iniciar sesión, launchd lo relanza si se cae
./scripts/dashboard.sh tailnet-on       # opcional: también en https://<mac>.<tailnet>.ts.net:8444 (solo tailnet, nunca Funnel)
./scripts/install_watchdog_agent.sh --run-now  # cada 15 min: DNS público Funnel + /mcp; recrea sidecar si falta (log ~/Library/Logs/mnemos-watchdog.log)
```

Cognee se publica en el host en `127.0.0.1:8010` (no 8000) para no pisar servidores de desarrollo locales que
suelen usar el 8000. Los gateways no cambian (usan `cognee:8000`
por la red interna).

Desinstalar: `launchctl bootout gui/$(id -u)/io.mnemos.cognify && rm ~/Library/LaunchAgents/io.mnemos.cognify.plist`,
`./scripts/dashboard.sh uninstall-agent`, y `./scripts/install_watchdog_agent.sh --uninstall`.

Para que la Mac no se duerma enchufada (y el hub y el backup nocturno sigan andando), una sola vez:

```bash
sudo pmset -c sleep 0 disksleep 0   # la pantalla se sigue apagando sola (displaysleep no se toca)
pmset -g custom                     # en "AC Power": sleep 0, disksleep 0
```

Ojo: si `RESTIC_REPOSITORY` apunta a una carpeta de la misma notebook (ej. `~/mnemos-backups`), eso te cubre de
errores y de un contenedor roto, pero no de perder la notebook. Copiá esa carpeta a un disco externo o a la nube
(o cambiá el repo a un destino externo) para cumplir el diseño.

Una vez por mes, probá un restore completo en otra carpeta o máquina.
