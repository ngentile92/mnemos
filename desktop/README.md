# Mnemos Desktop (MVP scaffold)

A small Tauri 2 app around the single-process hub (`mnemos-local`, see `docs/local-mode.md`):

- first-run wizard: your name, contexts, a password (only the scrypt hash is stored, in `~/Mnemos/auth.json`);
- start / stop the local hub (`mnemos-local serve` on 127.0.0.1:8765), status light, "Open Mnemos page";
- quitting the app stops the hub (no leftover processes);
- **Connect Claude Desktop**, one button for every space: adds ONE `mnemos` stdio server to
  `claude_desktop_config.json` (backs the file up first, replaces older `mnemos-<context>` entries this app wrote,
  never touches other servers). Claude uses its default space and can pass `context` for another one (see
  `docs/local-mode.md`, "One connector"). No URL or token to copy.
- **Connect apps** screen: one card per AI app with its status (found on this Mac / connected): Claude Desktop
  (button), **Cursor** (one-click install link, Cursor asks to confirm), **Claude Code**, **Codex** and **VS Code**
  (one Terminal line to copy). All run the same stdio connector (`mnemos-local stdio --home …`, every space), so no
  token is written into other apps' configs. Cloud apps (claude.ai, phone, ChatGPT, Grok) need *Reach from
  anywhere*. Headless: `Mnemos --app-cards` prints the cards as JSON (used by the friend test).

- **Menu bar icon** (Open my memory / Show / Quit); closing the window keeps Mnemos running.
- **Starts with your Mac** (LaunchAgent `~/Library/LaunchAgents/io.mnemos.desktop.plist`, opens in the background;
  on by default after setup, checkbox in the app). The hub starts on launch (`MNEMOS_AUTOSTART=0` skips it).
- **Keychain**: the local token is kept in the macOS Keychain (service `io.mnemos.desktop`) and passed to the hub as
  `MNEMOS_TOKEN`; an existing `~/Mnemos/token` is moved there. `MNEMOS_NO_KEYCHAIN=1` keeps the file. The
  password is never stored, only its scrypt hash.

**Self-contained:** release builds carry a relocatable Python (python-build-standalone) with `mnemos-hub`
preinstalled in `Mnemos.app/Contents/Resources/python` (`scripts/bundle_python.sh`, ~200 MB unpacked), so users
install nothing else. Runtime order: `MNEMOS_HUB_CMD` (dev override) → bundled Python → `uv` (dev builds only).
`MNEMOS_AUTOSTART=1` starts the hub on launch (used by the CI smoke test).

```bash
cd desktop && npx -y @tauri-apps/cli@2 dev      # run
npx -y @tauri-apps/cli@2 build                   # dev build (uses uv)
scripts/bundle_python.sh && npx -y @tauri-apps/cli@2 build --config src-tauri/tauri.bundle.conf.json  # self-contained
```

CI (`desktop` job) bundles Python, builds the unsigned `.app` + `.dmg`, launches the packaged app on a macOS runner
with uv removed and no Python on PATH, checks the hub answers MCP `initialize`, and uploads
`Mnemos-macos-unsigned` (`.dmg` + zipped `.app`).

## Not done yet (needs an Apple Developer account)

- **Signing + notarization**: needs an Apple Developer account (US$99/yr), a Developer ID certificate and an
  app-specific password in CI secrets. Unsigned builds open only via right-click → Open (or
  `xattr -dr com.apple.quarantine Mnemos.app`).
- Embedded dashboard, "Configure Cursor" button. The Claude config points at the
  app's location: moving Mnemos.app afterwards needs the Connect button pressed again.
