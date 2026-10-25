**Mnemos for Mac (local mode, no Docker).** Install guide: [docs/INSTALL-mac.md](https://github.com/ngentile92/mnemos/blob/main/docs/INSTALL-mac.md)

- `Mnemos-macos.dmg` — the app (unsigned: first open with right-click → Open).
- `Mnemos-macos.app.zip` — same app, zipped.
- `mnemos.mcpb` — old Claude Desktop extension, deprecated: not needed (use **Connect Claude Desktop** in the app) and kept only for older setups.

Newest first. Each `## vX.Y.Z` section is the body of that GitHub Release (`scripts/release_notes.py`).

## v0.17.1

**Opens cleanly after a download, and fixes from real first tries.**

- **No more "Mnemos is damaged"**: the Mac app is now signed (ad-hoc) and sealed, so a downloaded copy only shows the usual "unidentified developer" prompt. First open: right-click → Open, or System Settings → Privacy & Security → Open Anyway.
- **Connect an AI**: one card per app (Claude Desktop, ChatGPT, Claude on the web and phone, Cursor, Claude Code, Codex, Grok) with its status and how to connect it. ChatGPT and Claude on the web connect live through Reach from anywhere, or with two copy-and-paste steps.
- **Fill your memory from your past chats**: if your computer had little to import, copy one request into a connected AI and it saves what it already knows about you as notes you can review.
- **Empty scan explained**: Import now says, app by app, why nothing was found (for example, Claude and ChatGPT keep their memory in your account) and what to do next.
- **Everything clickable**: the More menu no longer hides behind the page, the Graph toolbar is fully visible, and import sources line up one per row. A new automatic check keeps every button reachable.

## v0.17.0

**A redesigned Mnemos: clearer screens, and a first run that gets you to "it knows me" fast.**

- **Simpler navigation**: Home, Memory, What each AI knows, Connect, Import, with the rest under More. Settings without jargon, styled controls, friendly empty states.
- **Import that remembers**: every source shows Imported ✓ (date · N facts), New, or N new files; review grouped by source, duplicates marked "already in your memory", and an import history with Undo.
- **New Memory screen**: ⌘K search, filters with counts (Show / App / Source / When), a dense list with multi-select and confirmed bulk actions, and a detail panel with plain-language history.
- **What each AI knows**: see each app's own memory files, compare them with Mnemos, import what's missing, or edit them with a diff (a backup is made first; nothing changes without your click), with Restore for earlier versions.
- **First run in four steps**: you → Save all → connect an app → ask it, and Mnemos confirms when your AI actually read your memory. Existing memories skip the welcome.
- **Polish everywhere**, fully in English and Spanish.

## v0.16.2

**Connecting your AIs, clearer.**

- **One clear state per app**: Not connected → Installed ✓ (send any message to finish) → Connected ✓, plus a one-line test question to check it.
- **Problems show in the app's own card**, with one button to fix or retry. A broken Claude Code install is named plainly, and Mnemos adds itself to Claude Code directly.
- **Old Claude extension off in one click** (backed up, nothing deleted). The optional Claude text now points to Settings → Account → Instructions for Claude, in plain words.
- **ChatGPT and Grok get their own cards** with the two copy/paste steps.
- **"What do you know about me?" answers from all your spaces**, not only the app's default one.

## v0.16.1

**Hotfix: the desktop app no longer gets stuck on "Starting…".**

- The desktop app's health check now sends the address and private token the server expects, so the status turns to "Mnemos is running" and **Open my memory** works.
- If Mnemos ever takes more than 30 seconds to start, the app says so and offers a **Restart Mnemos** button instead of waiting forever.

## v0.16.0

**A first run anyone can follow, in English or Spanish.**

- **Welcome screen**: asks your name once, shows your spaces, and a short demo (for new memories only) shows another AI app remembering what you just saved.
- **Home answers "is it working?" in five seconds**: one next step and one gentle memory suggestion.
- **Pick your AIs and connect in one click**: every local app connects from the desktop app with a live check; errors come with a button that fixes them.
- **Starter skills with Try it**, a cloud setup in two steps, and an About block.
- **Whole app in English or Spanish**, including messages with numbers (proper plurals), server and desktop errors, and full sentences. No jargon on any screen.

## v0.15.0

**Simpler and smarter without a model.**

- **Sorts notes into the right space with rules alone**: phrases like "I work at…", "Trabajo en…", "my wife…" or "mi jefe…" now decide the space (before, they stayed in the default space). Spaces named in other words (trabajo, vida, job…) get sensible defaults.
- **Catches contradictions without a model**, even across English and Spanish: "Prefers dark mode" vs "No me gusta el modo oscuro" is flagged when saving (the AI is asked to check with you) and in Memory health.
- **A real profile from the first notes**: in a new memory, who you are and your preferences go to your profile and people, goals and projects to their own topics, instead of one "notes" file.
- **Less to think about**: Reach from anywhere, the memory store switch, Labs and the memory model are folded into **Settings → Advanced**. The Graph tab sits next to Settings and never animates in the background (saves battery).
- **One Mnemos in Claude**: the old `.mcpb` extension is deprecated; Connect flags it and opens Claude so you can remove it.
- **Clear port message**: if another app (OpenMemory uses the same port) holds 8765, Mnemos says so instead of looking "running".
- Hub: rate limit per app and session, so parallel tool calls no longer fail (#135). Tool messages are in English only.

## v0.14.0

**New in 0.14: memory that keeps itself tidy — you decide every change.**

- **Memory health** (Memory tab): after every import, once a day, or on "Check now", Mnemos groups suggestions: not about you (pasted code, tables, logs), rewrite (third person), duplicates, contradictions, other space, probably wrong, out of date. Accept one or a whole group; contradictions you pick by hand. A backup is saved before anything is applied and "Undo last cleanup" puts it all back. Nothing changes without your click.
- **Cleaner saves**: pasted code, tables and logs are refused (the AI is asked for a one-sentence fact instead), third-person notes are saved first-person-neutral, and near-duplicates are skipped.
- **Works in files mode too**: the Memory health card stays visible when your memory is Markdown files, and the files list refreshes after a cleanup or undo.
- Fixes: editing a note now refreshes the profile; the third-person rule recognises any verb.

## v0.13.0

Your notes go to the right space, and you can see what each AI app remembers.

- Sorted by content: each new note (auto-capture from Claude Code, Cursor and Codex, Import, or an AI that names no space) goes to the space whose description fits it. The local model decides, with simple rules as a fallback. When unsure, the note stays in the default space marked "review". Recently learned shows where each note went and why, with a one-click Move.
- Folders: map a code folder to a space (Spaces → Folders). Whatever you say in a coding app inside that folder goes there.
- Better auto-capture: notes are written in your language, without "The person…/El usuario…", vague items are dropped and near-duplicates skipped (capture eval: precision 0.68 → 1.0 on the dev set).
- App memories: a new tab showing each app's own memory files (Claude Code, Codex, Cursor, chat exports) exactly as stored, read-only, with path, date and "Import into Mnemos" per section.
- Spaces with a one-word description now get a sensible default description for sorting.

## v0.12.4

Cleaner graph, round two.

- Also hides file paths, links, @handles and whole sentences that the model mistook for things.
- Folds "X API", "X repository", "X platform", "MCP de X", "base de datos de X" into X.

## v0.12.3

Graph, cleaner and alive.

- While the local model reads your notes, the graph comes alive: new things pop in with sparks, new links draw themselves, and a progress card shows notes done, % and time left, with playful messages built from what it just found.
- Cleaner map for humans: generic words, languages, code identifiers and dates are hidden; decorated names ("X project", "X.info", "X-E") and name variants with the same surname are merged into one node.
- `mnemos-local graph-rebuild` (with backup) also drops those noise links from the graph file.

## v0.12.2

- **A living map of your memory.** Each space (Personal, Work…) is its own galaxy; people, places and projects you mention in both sit between them as bridges. Continuous physics, glow, gradient links, size by importance, labels that don't pile up.
- **Explore interactions by Francisco Rua**, ported from the hub dashboard (#132, #133/#141): drag pulls neighbours, hide shared, open the notes behind a dot, relations read subject → object, one name per concept, "How to read this". Thank you, Francisco!
- **How it grew**: a time-lapse of your memory, oldest notes first. **Shift-click** two dots to see how they connect.
- **Present**, **PNG** and **Record** (video) for talks and posts. `MNEMOS_DEMO=1` opens a fictional demo memory (Ana, ~150 notes) so screenshots never show your own data.
- **Correct counts after a cleanup**: the graph counts notes from your memory itself and drops leftovers of deleted notes; `mnemos-local graph-rebuild` does it in one go (with a backup).

## v0.12.1

- **No more Keychain password prompts.** The local token now lives in `~/Mnemos/token` (0600). Before, the app was ad-hoc signed and its signature changed with every build, so macOS forgot "Always Allow" and asked 2–4 times per start and on "Open my memory".
- **The Graph is back.** The Graph tab is visible again by default (Labs stays optional for the extra model features) and works with both note stores.

## v0.12.0

**New in 0.12: built-in memory for coding agents (opt-in).**
- Mnemos → Apps → *Built-in memory for coding agents*: write your profile where each coding agent already looks, so
  every session starts knowing you even before it calls Mnemos. **Claude Code**: `~/.claude/rules/mnemos.md`.
  **Codex**: a marked block in `~/.codex/AGENTS.md` (the rest of the file is left alone). **Cursor**: a local plugin
  rule (`~/.cursor/plugins/local/mnemos`), since Cursor has no documented global rules file.
- Off by default, per agent, only for the spaces you pick. Mnemos keeps a backup of any file before it first changes it,
  rewrites its part only when your profile changes, never reads edits back from those files, and removes exactly what
  it wrote when you turn it off.

## v0.11.0

**New in 0.11: simple memory — your memory as Markdown files you can read.**
- **Memory as files.** Each space is a folder with a short `profile.md` (read at the start of every chat) and a few
  topic files (`people.md`, `projects.md`, `goals.md`, `how-i-work.md`, …). Every change is a version in a built-in
  git history, signed by the app that made it (Claude, Cursor, … or "you" when you edit a file by hand), so anything
  can be restored. New installs start on files; existing installs keep working as before until you switch.
- **Six tools instead of twenty.** With simple memory on, AI apps see `memory_start`, `memory_read`, `memory_search`,
  `memory_remember`, `memory_edit` and `memory_forget` (plus spaces and skills). `memory_remember` refuses secrets and
  task details unless you asked; `memory_edit` keeps a changed fact under "No longer true" instead of losing it.
- **Switch when you want, switch back if you don't like it.** Memory → *Switch to simple memory*: **Preview** shows
  exactly which files you would get (nothing changes), **Switch** keeps every note id, and Settings → *Simple memory*
  switches back, copying anything new back first. Same from a terminal: `mnemos-local migrate preview|apply|undo`.
- **Memory screen as files.** Browse each space's files, edit them in place (with a check so two edits never
  overwrite each other), see each file's history and restore any version.
- **Labs.** The Graph, people/project pages, the "still true?" check and the nightly tidy-up of the memory model are
  now **Labs**: kept and maintained, off by default. Turn them on in Settings → Labs (or `MNEMOS_LABS=1`).
- Also in this release (thanks @Francisco-Rua): `memory_update` survives a busy Cognee, tags survive a Cognee rebuild,
  hygiene sees tags and source apps, a tailnet-only exposure mode for the hub (`MNEMOS_EXPOSURE=tailnet`), Linux hosts
  without a venv, Ollama-in-Docker docs, and a more readable Explore graph.

## v0.10.2

- Entity typing on the memory model, second pass: a new person needs a person cue next to the name; names that open sentences in several notes count; git commit trailers (Co-authored-by) are ignored; acronyms and technical terms (OAuth, RLS) are topics; the context name is a background org, never the subject.

## v0.10.1

- Entity typing on the memory model: well-known tools (GitHub, ClickUp, Claude, Tailscale, …) are typed `tool` and never become the subject of a fact just for being named in it; extend the list with `MNEMOS_KNOWN_TOOLS`.
- Acronyms (PR, API), document words (Instructions), languages and dates are no longer taken as names; sentence-initial capitals are ignored.
- Fewer `unknown` kinds: instructions, habits, skills and project state are recognised.

## v0.10.0

**New in 0.10: the memory model on the full Docker hub.**
- The hub can now use the same memory model as the local app: entities, typed facts, the nightly tidy-up, the
  profile and better ranking. It is stored in a separate file per context, next to the ledger, and never rewrites
  Cognee notes. It is off unless you set `HUB_MEMORY_MODEL=1`.
- `python -m hub_gateway.model_admin` (run inside a gateway container) links existing notes with a dry run first and
  an exact undo. It also runs the tidy-up (each run can be undone) and prints the profile, entities and the weekly
  "still true?" list. See `docs/memory-model-hub.md`.

## Earlier versions (0.3 – 0.9.1)

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
