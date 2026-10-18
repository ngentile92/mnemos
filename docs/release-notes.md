**Mnemos for Mac (local mode, no Docker).** Install guide: [docs/INSTALL-mac.md](https://github.com/ngentile92/mnemos/blob/main/docs/INSTALL-mac.md)

- `Mnemos-macos.dmg` — the app (unsigned: first open with right-click → Open).
- `Mnemos-macos.app.zip` — same app, zipped.
- `mnemos.mcpb` — Claude Desktop extension (optional; the app's "Connect to Claude Desktop" button is enough).

**New in 0.10: the memory model on the full Docker hub.**
- The hub can now use the same memory model as the local app: entities, typed facts, the nightly tidy-up, the
  profile and better ranking. It is stored in a separate file per context, next to the ledger, and never rewrites
  Cognee notes. It is off unless you set `HUB_MEMORY_MODEL=1`.
- `python -m hub_gateway.model_admin` (run inside a gateway container) links existing notes with a dry run first and
  an exact undo. It also runs the tidy-up (each run can be undone) and prints the profile, entities and the weekly
  "still true?" list. See `docs/memory-model-hub.md`.

**New in 0.9.1: Claude Desktop saves on its own, and fewer invented facts.**
- **Claude Desktop.** Claude Desktop ignores the rules an MCP server sends, so the save rules now live in the tool
  descriptions (with examples), every search/profile result reminds the model to save lasting facts, and a **Use my
  memory** prompt is in Claude's "+" menu. Mnemos Desktop's Claude card gives a short text to paste once in Claude →
  Settings → Profile (the most reliable fix) and warns when the Mnemos extension is installed as well (two memories).
  After updating, quit and reopen Claude Desktop. See `docs/claude-desktop.md`.
- **Model check for invented facts.** The local model (or Apple's on-device model) checks each extracted fact against
  what you wrote. A fact it cannot back goes to the inbox (live capture) or to the end of the import review instead
  of being saved.

**New in 0.9: a memory that knows who and what each note is about.**
- **Memory model.** Every note is linked to the person, project, place or tool it is about, with a kind, importance,
  how long it lasts and where it came from. Writes only ever add. Settings → Memory model lets you try linking your
  existing notes first (no changes), then do it for real and undo it.
- **Nightly tidy-up.** When a newer fact replaces an old one ("switched from X to Y"), the old one is kept as *no longer
  true* and leaves answers. Time-bound facts expire, and things you never use fade. Each entity gets a short summary
  that cites its notes, and each space gets a profile. You can undo every run.
- **Better search.** Results are ranked by importance, use, whether the fact is still current, and the people or
  projects your question names. Old facts come back only when you ask about the past. Each result says why it was
  picked.
- **Grounding check.** A fact the model extracted is dropped when its quote is not in what you wrote, or when it names a
  person, place, product or number you never mentioned.
- **New screens.** *What your AI knows about you* (the profile) comes first, with a page for each person or project.
  *Still true?* asks you to keep, fix or forget important old notes. *Why this?* and *Not relevant* appear on every
  note. The Graph now shows only people, projects and things.

**New in 0.8: coding apps teach Mnemos as you work.**
- **Auto-capture for Claude Code, Cursor and Codex** (Apps → Auto-capture, opt-in). A small hook keeps each message
  you send in a local queue on this computer. It runs no AI and takes under a second, and paused apps are skipped.
  New sessions start with your profile. Codex asks you to trust the hooks once (`/hooks`). Turning auto-capture off
  removes the hooks, and a backup of each file is kept.
- **Learned when the chat goes quiet.** After 15 idle minutes, the local model reads the session and keeps lasting
  facts. Clear ones go straight to **Recently learned** (Notes), where each has an Undo. Only possible
  contradictions or unsure facts go to a short inbox (at most 20 a day).
- **`memory_profile`**: a new tool that gives an AI who you are in a few lines. Instructions now ask apps to call it at
  the start of every conversation.

**New in 0.7: memory that keeps what lasts and drops the noise.**
- **Write filter.** Before anything is saved, Mnemos drops trivia (paths, URLs, ports, errors, code, ids, "thanks",
  what the assistant said, task logs, one-off questions, encyclopedic facts). Facts that only hold for a while
  ("this week", "right now") get an expiry date. In the synthetic eval (repo `eval/`), precision of what is kept went
  from 0.40 to 0.75 on 80 held-out examples the rules were never tuned on; no durable fact was lost.
- **Sensitive topics are off by default** (health, religion, politics, sexuality, money, ID numbers, relationship
  crises). You can turn them on per import.
- **Import mines one conversation at a time**, keeping its date, app and a running topic. Candidates come typed
  (preference, decision, person, project, goal…) with importance and how long they last, and are grouped by subject.
- **Sharper instructions for AI apps.** What to save and what never to save comes first. `memory_save` warns when a
  note looks like a task detail. Apps → Activity shows counts per app.
- **Harvest what other AIs already learned:** Claude Code auto-memory, Codex memories, Cursor rules, CLAUDE.md and
  Claude/ChatGPT exports show up in Import. *Ask Grok* lets you paste a memory list from Grok or ChatGPT.

**New in 0.6: one Mnemos for every AI app, sharing the same memory.**
- **One connector, all your spaces.** Claude Desktop, Cursor, Claude Code, Codex and VS Code each get a single
  `mnemos` entry; every tool takes an optional `context` and uses the app's default space otherwise. *Connect
  Claude Desktop* replaces the old one-per-space entries (backup kept next to Claude's config). If you installed
  the older Mnemos extension (`.mcpb`) in Claude, remove it in Settings → Extensions.
- **Connect apps** in Mnemos Desktop: Claude Desktop button, one-click Cursor install link, and a line to paste
  in Terminal for Claude Code, Codex and VS Code.
- **Spaces** screen: rename, colour, description (what the AI reads to decide where things go), what Shared means,
  and read-only bridges as on/off toggles.
- **Apps** screen: each app's default space and allowed spaces, **Pause**, **Remove access**, and an **Activity**
  feed of what each app read or saved.
- **Graph**: every note (pinned and hand-written first), "me" collapsed into one hidden node, duplicate names
  merged, types and groups, directed labelled edges, All spaces view.
- **Reach from anywhere** (optional, off by default): Claude on the web and phone, ChatGPT and Grok can use your
  memory through Tailscale Funnel, signing in with your Mnemos password and only for the spaces you tick. Works
  only while your Mac is awake and Mnemos is running.

**New in 0.5: a better private model, your whole history, and search over what you said.**
- Built-in model is now Qwen3 4B Instruct (2.5 GB; Qwen3 1.7B on Macs with 8 GB RAM or less) and writes facts
  itself. In a benchmark on real chats it found ~7x more useful facts than 0.4's model. If you already downloaded
  the 0.4 model, the new one downloads in the background at start; the old one keeps working until then and is
  deleted after.
- Apple Intelligence (macOS 26 with Apple Intelligence on): used for import with nothing to download when the
  built-in model is not installed (Settings → Apple Intelligence).
- Import → **Full history**: reads every chat of the selected sources in the background, resumes after you quit,
  next time reads only what is new. Nothing is saved until you approve it.
- Your own messages become searchable: memory_search adds `from_sources` passages next to saved notes
  (Import → Full history → Forget source messages deletes them).
- Duplicates by meaning use EmbeddingGemma's similarity prompt (benchmark: 100% vs 70% before); search uses its
  query prompt.
- Graph view: entities and relations found by the local model (built in the background, incremental).

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
