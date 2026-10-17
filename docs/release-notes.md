**Mnemos for Mac (local mode, no Docker).** Install guide: [docs/INSTALL-mac.md](https://github.com/ngentile92/mnemos/blob/main/docs/INSTALL-mac.md)

- `Mnemos-macos.dmg` — the app (unsigned: first open with right-click → Open).
- `Mnemos-macos.app.zip` — same app, zipped.
- `mnemos.mcpb` — Claude Desktop extension (optional; the app's "Connect to Claude Desktop" button is enough).

**0.4.1:** cleanup no longer flags the same fact in two languages as a contradiction (built-in model: 8/8 on a real-model probe, was 7/8); clean quit with the built-in model loaded.

**New in 0.4: works well without Ollama.** "Improve my memory" downloads a small private model (Qwen2.5 1.5B
Instruct + EmbeddingGemma 300M, ~1.4 GB, resumable, checksummed, runs on the Mac) for import, cleanup and
search/duplicates. Optional OpenAI/Anthropic key in Settings (Keychain, per feature, clearly marked as leaving the
Mac). The Engine badge shows Rules / Built-in / Ollama / API.

**0.3: Scan this Mac.** On first run (or Import → Scan this Mac) Mnemos finds what you already told
Claude Code, Cursor, CLAUDE.md/AGENTS.md, Obsidian and ChatGPT/Claude.ai exports, shows a preview, and imports
only what you approve, with one-click undo. Read-only, secrets stripped, `.env` never read.

Also in local mode: "Open my memory" (notes, graph, skills), import from ChatGPT / Claude / Markdown, duplicate and
contradiction check on save, nightly cleanup.
