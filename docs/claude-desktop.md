# Claude Desktop: getting it to save on its own

**Why it may not save.** Claude Desktop connects to Mnemos and lists its tools, but it does **not** pass the MCP
server instructions (the "save lasting facts without being asked" rules) to the model
([anthropics/claude-code#23808](https://github.com/anthropics/claude-code/issues/23808)). Claude Code and Cursor do,
and coding apps can also use the auto-capture hooks; Claude Desktop has no hooks. So in Claude Desktop the model only
sees each tool's description and decides on its own whether to call it.

**What Mnemos does about it (0.9.1).**
- `memory_save`, `memory_profile` and `memory_search` carry the rules in their descriptions, with examples
  ("me gustan los caballos" → "Likes horses.").
- Every `memory_search` / `memory_profile` result ends with a one-line reminder to save lasting facts.
- An MCP prompt, **Use my memory** (Claude's "+" menu → Mnemos), starts a chat with your profile and the rules.
- Mnemos Desktop → Claude Desktop card: a ready-made text to paste once in **Claude → Settings → Profile →
  personal preferences**, which Claude reads in every chat. This is the most reliable fix.
- It warns when the Mnemos **extension** (.mcpb) is installed as well as the `mnemos` entry: Claude then sees two
  memories, and an old extension can run an older Mnemos. Keep one (remove the extension in Settings → Extensions).

**After updating Mnemos, quit Claude Desktop (Cmd+Q) and reopen it**: Claude keeps running the Mnemos process it
started, so it still runs the old version until it restarts.

**Claude conversations you did not save.** Claude has no local history Mnemos can read. Export your data
(claude.ai → Settings → Privacy → Export data) and drop the zip in Mnemos → Import from time to time; Mnemos mines only
conversations it has not seen before.
