# Install Mnemos on a Mac (5 minutes, no technical skills)

Mnemos gives your AI apps (Claude, ChatGPT, Cursor, Codex…) one memory that you own. Everything stays on your Mac.
*[Versión en español más abajo](#instalar-mnemos-en-una-mac-español).*

## 1. Download
Download **[Mnemos-macos.dmg](https://github.com/ngentile92/mnemos/releases/latest/download/Mnemos-macos.dmg)**,
open it and drag **Mnemos** into **Applications**.

## 2. Open it the first time (important)
The app is not signed by Apple yet, so a normal double-click shows *"cannot be opened"*. Do this once:
1. Open **Applications**, **right-click** (or Control-click) **Mnemos** → **Open**.
2. Click **Open** again in the warning.

From then on it opens normally. If you only see *"Move to Trash"*: go to **System Settings → Privacy & Security**,
scroll down and click **Open Anyway** next to Mnemos.

## 3. Set it up
The **Welcome** screen asks for your name, your spaces (*Contexts*: separate memories that never mix, e.g.
`personal` and `work`) and a password (only used if you later connect from your phone or the web). Keep the
defaults if unsure and click **Create my Mnemos**.

When it is ready you see a green **Mnemos is running**, and the Mnemos icon appears in the menu bar (top right).
Closing the window keeps it running; **Quit Mnemos** is in the menu bar icon.

![Mnemos running, with the Connect buttons](img/install-1-app.png)

## 4. Connect your AI apps
Under **Connect apps**, click **Connect Claude Desktop**. Then quit Claude completely (**Cmd+Q**) and open it again.
Ask Claude *"what do you know about me?"*: it now reads and saves to Mnemos on its own.

- **Cursor**: click **Connect Cursor**, then **Install** in Cursor.
- **Claude Code, Codex, VS Code**: click **Copy** and paste the line in Terminal once.
- **Claude on the web or phone, ChatGPT, Grok** (optional): **Open my memory → Settings → Advanced → Reach from anywhere** (needs
  the free Tailscale app). Off until you turn it on; those apps only work while this Mac is awake and Mnemos is
  running. Details: [local-mode.md](local-mode.md#reach-from-anywhere-06-opt-in).

Every app sees all your spaces and saves to its default one (change it in **Open my memory → Apps**).

## 5. See and fix your memory
Click **Open my memory**. The tabs: **Notes** (search, edit, forget with undo, and *Memory health*), **Skills**,
**Spaces**, **Apps**, **App memories** (what each app stores on its own), **Import**, **Graph** and **Settings**.

### Scan this Mac (first big import)
The first time your memory is empty, Mnemos offers to **Scan this Mac** (also under **Import**). It looks, read-only,
for what you already told other AIs: Claude Code sessions, `CLAUDE.md` / `AGENTS.md` files, Cursor chats and rules,
Obsidian vaults, and ChatGPT / Claude.ai export zips in `Downloads`. You pick sources and spaces, see a preview,
and nothing is saved until you click **Save**; **Undo this import** removes the whole batch. Anything that looks
like a password, token or key is dropped, and `.env` files are never opened. For memories that live only in the
cloud, the **Claude.ai / ChatGPT** buttons copy a prompt, open the site, and give you a box to paste the answer.

### Improve my memory (optional private model)
Mnemos can **download a small private AI model** once (it runs on your Mac, nothing is sent anywhere). It finds
better facts on import, sorts notes into spaces and spots duplicates and contradictions. Click **Not now** and Mnemos
keeps working with simple rules; download it later in **Settings → Improve my memory**. If you run
[Ollama](https://ollama.com), Mnemos uses it first. The **Engine** badge shows what is in use: Rules, Built-in,
Ollama or API.

**Good to know:** addresses ending in `/mcp` are for AI apps, not for your browser. Your memory lives in the
`Mnemos` folder in your home folder; back it up like any other folder.

## Troubleshooting

| What you see | What to do |
|---|---|
| *"Mnemos cannot be opened because the developer cannot be verified"* | Right-click → **Open** (step 2). Or **System Settings → Privacy & Security → Open Anyway**. |
| *"Mnemos is damaged and can't be opened"* | The download was quarantined. In Terminal: `xattr -cr /Applications/Mnemos.app`, then open it again. |
| *"port 8765 is already in use"* | Another Mnemos (or another app on that port) is running. Quit it from the menu bar icon (**Quit Mnemos**) and open Mnemos again. |
| Claude does not use Mnemos | Quit Claude with **Cmd+Q** (closing the window is not enough) and reopen. If you moved Mnemos.app after connecting, click **Connect** again. |
| Claude shows Mnemos twice | An old Mnemos extension (`.mcpb`) is also installed. Remove it in Claude → **Settings → Extensions**. |
| A page says *"This address is for AI apps"* | Normal: `/mcp` addresses are for AI apps, not your browser. Use **Open my memory**. |
| The memory page asks for a password | Open it from the app or the menu bar (**Open my memory**), or type the password you chose at setup. |
| A box asks for the **Keychain** | Only if you saved an OpenAI/Anthropic key in **Settings**: click **Always Allow**. |
| I want to start over | Quit Mnemos, delete the `Mnemos` folder in your home folder, open the app again. This erases your memory. |

Logs: `~/Mnemos/hub.log`. Report problems at https://github.com/ngentile92/mnemos/issues.

---

# Instalar Mnemos en una Mac (español)

Mnemos le da a tus apps de IA (Claude, ChatGPT, Cursor, Codex…) una sola memoria que es tuya. Todo queda en tu Mac.

## 1. Descargá
Descargá **[Mnemos-macos.dmg](https://github.com/ngentile92/mnemos/releases/latest/download/Mnemos-macos.dmg)**,
abrilo y arrastrá **Mnemos** a **Aplicaciones**.

## 2. Abrilo la primera vez (importante)
La app todavía no está firmada por Apple, así que un doble clic muestra *"no se puede abrir"*. Hacé esto una vez:
1. En **Aplicaciones**, **clic derecho** (o Control-clic) sobre **Mnemos** → **Abrir**.
2. Tocá **Abrir** otra vez en el aviso.

Si solo aparece *"Mover a la papelera"*: **Ajustes del Sistema → Privacidad y seguridad**, bajá y tocá
**Abrir igualmente** al lado de Mnemos.

## 3. Configuralo
La pantalla **Welcome** pide tu nombre, tus espacios (*Contexts*: memorias separadas que no se mezclan, por ejemplo
`personal` y `trabajo`) y una contraseña (solo se usa si después conectás desde el celular o la web). Si dudás,
dejá lo que viene y tocá **Create my Mnemos**.

Cuando está listo ves **Mnemos is running** en verde y el ícono de Mnemos en la barra de menú (arriba a la derecha).
Cerrar la ventana no lo apaga; **Quit Mnemos** está en el ícono de la barra de menú.

## 4. Conectá tus apps de IA
En **Connect apps**, tocá **Connect Claude Desktop**. Cerrá Claude del todo (**Cmd+Q**) y abrilo de nuevo.
Preguntale *"¿qué sabés de mí?"*: desde ahora lee y guarda en Mnemos solo.

- **Cursor**: tocá **Connect Cursor** y después **Install** en Cursor.
- **Claude Code, Codex, VS Code**: tocá **Copy** y pegá la línea una vez en la Terminal.
- **Claude web o celular, ChatGPT, Grok** (opcional): **Open my memory → Settings → Advanced → Reach from anywhere** (necesita la
  app gratuita Tailscale). Viene apagado; esas apps solo funcionan mientras la Mac está despierta y Mnemos abierto.

Cada app ve todos tus espacios y guarda en el suyo por defecto (se cambia en **Open my memory → Apps**).

## 5. Mirá y corregí tu memoria
Tocá **Open my memory**. Las pestañas: **Notes** (buscar, editar, olvidar con deshacer, y *Memory health*),
**Skills**, **Spaces**, **Apps**, **App memories** (lo que cada app guarda por su cuenta), **Import**, **Graph** y
**Settings**.

### Scan this Mac (primera importación)
Con la memoria vacía, Mnemos ofrece **Scan this Mac** (también en **Import**). Busca, sin modificar nada, lo que ya
les contaste a otras IAs: sesiones de Claude Code, archivos `CLAUDE.md` / `AGENTS.md`, chats y reglas de Cursor,
bóvedas de Obsidian y exportaciones de ChatGPT / Claude.ai en `Descargas`. Elegís fuentes y espacios, ves una vista
previa y no se guarda nada hasta que tocás **Save**; **Undo this import** borra toda la tanda. Lo que parece una
contraseña, token o clave se descarta y los archivos `.env` nunca se abren. Para memorias que están solo en la nube,
los botones **Claude.ai / ChatGPT** copian una pregunta, abren el sitio y te dan un cuadro para pegar la respuesta.

### Improve my memory (modelo privado opcional)
Mnemos puede **descargar una vez un modelo de IA chico y privado** (corre en tu Mac, no manda nada afuera). Encuentra
mejores datos al importar, ordena las notas en espacios y detecta repetidas y contradicciones. Con **Not now** sigue
funcionando con reglas simples; lo podés bajar después en **Settings → Improve my memory**. Si usás
[Ollama](https://ollama.com), Mnemos lo usa primero. El indicador **Engine** muestra qué se usa: Rules, Built-in,
Ollama o API.

**Bueno saber:** las direcciones que terminan en `/mcp` son para apps de IA, no para el navegador. Tu memoria está
en la carpeta `Mnemos` de tu carpeta personal; hacele backup como a cualquier carpeta.

## Si algo falla

| Qué ves | Qué hacer |
|---|---|
| *"No se puede abrir Mnemos porque no se puede verificar el desarrollador"* | Clic derecho → **Abrir** (paso 2). O **Ajustes del Sistema → Privacidad y seguridad → Abrir igualmente**. |
| *"Mnemos está dañado y no se puede abrir"* | La descarga quedó en cuarentena. En Terminal: `xattr -cr /Applications/Mnemos.app` y abrilo de nuevo. |
| *"port 8765 is already in use"* | Hay otro Mnemos (u otra app en ese puerto) abierto. Cerralo desde el ícono de la barra de menú (**Quit Mnemos**) y abrí Mnemos de nuevo. |
| Claude no usa Mnemos | Cerrá Claude con **Cmd+Q** (cerrar la ventana no alcanza) y abrilo. Si moviste Mnemos.app después de conectar, tocá **Connect** otra vez. |
| Claude muestra Mnemos dos veces | Hay una extensión vieja de Mnemos (`.mcpb`). Sacala en Claude → **Settings → Extensions**. |
| Una página dice *"This address is for AI apps"* | Normal: las direcciones `/mcp` son para apps de IA. Usá **Open my memory**. |
| La página de memoria pide contraseña | Abrila desde la app o la barra de menú (**Open my memory**), o escribí la contraseña que elegiste al configurar. |
| Aparece un cuadro del **Llavero** | Solo si guardaste una clave de OpenAI/Anthropic en **Settings**: tocá **Permitir siempre**. |
| Quiero empezar de cero | Cerrá Mnemos, borrá la carpeta `Mnemos` de tu carpeta personal y abrí la app. Esto borra tu memoria. |

Registro: `~/Mnemos/hub.log`. Problemas: https://github.com/ngentile92/mnemos/issues.
