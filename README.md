# Mnemos

**One memory for all your AIs.** Tell Claude something once and ChatGPT, Cursor, Codex or Grok already know it.
Connect a new AI and it knows you from the first message. Everything stays on your computer.

> **Get it for your Mac:** download **[Mnemos-macos.dmg](https://github.com/ngentile92/mnemos/releases/latest/download/Mnemos-macos.dmg)**
> and follow the [5-minute install guide](docs/INSTALL-mac.md). No Docker, no API keys, nothing else to install.
> First open: right-click **Mnemos** → **Open**, or **System Settings → Privacy & Security → Open Anyway** (the app is not notarized by Apple yet).

## What it does

- **One memory, every AI app.** Claude Desktop, Claude Code, Cursor, Codex and VS Code connect with one click or one
  pasted line. Claude on the web or phone, ChatGPT and Grok can connect too (optional, see *Reach from anywhere*).
- **Separate spaces.** Personal and work (or any spaces you create) never mix. Each note goes to the space whose
  description fits it; when Mnemos is unsure it asks you to review.
- **It knows how to use itself.** Every connected AI gets short instructions and your profile when the chat starts,
  so you never explain Mnemos or yourself again.
- **It brings in what your AIs already know.** *Scan this Mac* and *Import* read what you told Claude Code, Cursor,
  ChatGPT or Claude.ai, show a preview, and save only what you approve (one-click undo). *App memories* shows each
  app's own memory files exactly as they are.
- **It writes back.** Optionally, Mnemos keeps your profile in the files coding agents already read
  (Claude Code, Codex, Cursor), so a session knows you even before it calls Mnemos.
- **Memory that stays clean.** Pasted code and logs are refused, duplicates are skipped, and *Memory health*
  suggests what to delete, rewrite, merge or move. Nothing changes without your click, and every cleanup can be undone.
- **Skills.** Reusable instructions ([Agent Skills](https://agentskills.io), `SKILL.md`) that every connected AI can use.
- **Graph.** A living map of the people, places and projects in your memory (a secondary tab).

Your memory is plain files in the `Mnemos` folder in your home folder: readable Markdown, versioned, easy to back up.
An optional small private model (downloaded once, runs on your Mac) makes import and sorting smarter; without it
Mnemos uses simple rules.

## How it works

Mnemos is a small local [MCP](https://modelcontextprotocol.io) server (`127.0.0.1:8765`) inside a menu-bar app.
AI apps talk to it through these tools:

| Tool | What it does |
|---|---|
| `memory_start` | Profile + how to use Mnemos, read at the start of a chat |
| `memory_search` / `memory_read` | Find or read what you know |
| `memory_remember` | Save something new (Mnemos picks the space and the topic file) |
| `memory_edit` / `memory_forget` | Fix or remove a fact (with history and undo) |
| `skills_list` / `skills_get` | Your skills |
| `hub_whoami` / `hub_use_context` | Which spaces this app can see; switch space |

Memory and skills are treated as data, never as instructions. Details: [local mode](docs/local-mode.md),
[editing memory](docs/memory-editing.md), [what is isolated](docs/memory-trust.md), [security](SECURITY.md).

## Advanced

- **Self-hosted Docker hub** (always-on server with a knowledge graph, injected API credentials, Tailscale Funnel,
  GitHub or built-in login): [docs/HUB.md](docs/HUB.md).
- Python package and CLI (`mnemos-local`): see [local mode](docs/local-mode.md). Release history:
  [docs/release-notes.md](docs/release-notes.md).
- Windows and Linux desktop apps are not available yet.

## Contributing & license

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Licensed under the [MIT License](LICENSE). Third-party: Cytoscape.js
(MIT, vendored); the Docker hub pulls Cognee (Apache-2.0), Infisical (MIT core), Vaultwarden (AGPL-3.0, unmodified
image) and Tailscale images.

---

## En español

**Mnemos: una sola memoria para todas tus IAs.** Lo que le contás a Claude ya lo saben ChatGPT, Cursor, Codex o
Grok, y una IA nueva te conoce desde el primer mensaje. Todo queda en tu computadora, separado por espacios
(personal, trabajo…).

Para la Mac: descargá [Mnemos-macos.dmg](https://github.com/ngentile92/mnemos/releases/latest/download/Mnemos-macos.dmg)
y seguí la [guía de instalación](docs/INSTALL-mac.md#instalar-mnemos-en-una-mac-español) (5 minutos, sin Docker ni
claves). Primera vez: clic derecho sobre **Mnemos** → **Abrir**, o **Ajustes del Sistema → Privacidad y seguridad →
Abrir igualmente**. El hub con Docker para servidores está en [docs/HUB.md](docs/HUB.md) (guías en español: `docs/*.es.md`).
Licencia MIT.
