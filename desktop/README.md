# Mnemos Desktop (MVP scaffold)

A small Tauri 2 app around the single-process hub (`mnemos-local`, see `docs/local-mode.md`):

- first-run wizard: your name, contexts, a password (only the scrypt hash is stored, in `~/Mnemos/auth.json`);
- start / stop the local hub (`mnemos-local serve` on 127.0.0.1:8765), status light, "Open Mnemos page";
- quitting the app stops the hub (no leftover processes).

The hub runs through **uv** for now (`uv tool run --from mnemos-hub@git… mnemos-local`): the app looks for
`uv` in `~/.local/bin`, Homebrew and PATH. Phase 1 replaces this with a bundled sidecar so nothing has to be
installed. Dev override: `MNEMOS_HUB_CMD=/path/to/venv/bin/mnemos-local`.

```bash
cd desktop && npx -y @tauri-apps/cli@2 dev      # run
npx -y @tauri-apps/cli@2 build                   # unsigned .app/.dmg in src-tauri/target/release/bundle
```

CI (`desktop` job) builds the unsigned macOS app on every PR and keeps it as an artifact.

## Not done yet (needs Nico / Apple)

- **Signing + notarization**: needs an Apple Developer account (US$99/yr), a Developer ID certificate and an
  app-specific password in CI secrets. Unsigned builds open only via right-click → Open (or
  `xattr -dr com.apple.quarantine Mnemos.app`).
- Tray icon, autostart, Keychain, bundled sidecar, embedded dashboard, ".mcpb" / "Configure Cursor" buttons.
