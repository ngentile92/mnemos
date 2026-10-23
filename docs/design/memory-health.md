# Memory health: memory that stays clean by itself

Status: implemented in 0.14 (capture guard, health checks, Memory health screen). Files mode: see "Open items".

## Problem

Assistants save things that are not durable facts about the person: pasted code and tables, task steps, notes
written about "the user" in the third person, the same fact twice, facts that contradict each other, notes in the
wrong space, and notes that are simply wrong (about the assistant, a role-play character, sample data). Cleaning this
by hand does not scale and must never be done by someone other than the user. Mnemos should prevent most of it at
capture and turn the rest into suggestions that **the user** accepts or rejects in the app.

Rules are generic. Nothing in them names a person, a company or a project. The only personal input is the user's
own config: the owner name and the spaces (with their descriptions) in `contexts.yaml`.

## 1. Prevention at capture (`memory_save`, import, live capture)

| Check | What happens | Where |
|---|---|---|
| Pasted block (fenced code, 2+ table rows, several log lines, mostly code lines) | Not saved. The AI gets `status: "rejected"` and is asked to save one sentence with what matters | `memfilter.pasted_block`, `app._save_impl` |
| Third person ("The user prefers…", "El usuario…", "<Owner name> <any verb>…") | Saved as "Prefers…"; the reply carries `saved_text` | `memfilter.tidy` |
| Owner name + any verb | Any lowercase word after the name counts as the verb (so "utiliza", "maneja", "codes" work), except connectors (y / and / de / with …) and second names ("Ana y Juan son…" is kept) | `memfilter.tidy` |
| Code fragments, paths, task logs, assistant narration | Saved (the user may have asked), but the AI is told it looks like a task detail; the health check proposes forgetting it | `memfilter.classify` |
| Near-duplicate of an existing note | Not saved; the AI is told to use `memory_update` on that id | `_save_check` |
| Possible contradiction with an existing note | Saved, the AI gets `similar_notes` and is told to correct or mark obsolete | `_save_check` + `lexical_conflict` |

Import and live capture already run `memfilter.gate` + `polish` (same rules, plus grounding against the source).

## 2. Fixing what is already there: the Memory health screen

The Desktop app's Memory tab has a **Memory health** card with one tile per group:

| Group | Found by | Accept does |
|---|---|---|
| Not about you (junk) | `memfilter` rules + `pasted_block` | forget the note |
| Rewrite | `tidy` with the owner's name | replace the text |
| Duplicates | word overlap, or meaning (embeddings) when available | merge into one note, forget the copy |
| Disagree | local model judge, else numbers/negation rule | user picks which one is right (no bulk) |
| Other space | local model routing, else keyword rules on space descriptions; only ≥70 % sure, never to shared | move the note |
| Probably wrong | local model only (about the assistant, a character, sample data, someone else) | forget, or keep but hide from answers |
| Out of date | not touched for a year | mark obsolete |

Each suggestion shows the note, why it was flagged and, for rewrites and merges, the new text. Buttons: the main
action and **Keep as is** (never suggested again). Each tile has **Review** (filter to that group) and **Accept all N**.

Safety:

- **Backup first.** Every apply (one item or a bulk accept) copies the notes database, each space's model/graph/
  search databases and the Markdown memory to `<home>/data/backups/health-<timestamp>/` (last 5 kept).
- **Undo.** Every apply is recorded as a batch. **Undo last cleanup** reverts the whole batch through the normal
  tools (`memory_undo`, un-obsolete, delete the moved copy) and puts the suggestions back in the list. Each note also
  keeps its own version history (Undo in the note panel).
- Nothing is ever applied without a click.

## 3. When it runs

- After every import that saved something (Discover → commit kicks a background check).
- Once a day (the existing nightly loop in `serve`, around 03:00 or at start if the last report is older than a day).
- On demand: **Check now**.
- `mnemos-local hygiene` from a terminal.

The report lives in `<home>/data/hygiene.json`, decisions in `hygiene-state.json`, batches in
`health-batches.json` (all 0600, never leave the machine).

## 4. Local model or rules

The checks use whatever `local_engine` picks for "hygiene": an API key only if the user enabled it for cleanup,
else Ollama, else the built-in model. With no model everything still works with rules: junk, rewrite, duplicates
(words), contradictions (numbers/negation) and space moves (keywords). "Probably wrong" needs a model and is skipped
without one. A model failure mid-run falls back to the rules for the rest of the run.

## Open items

- Files mode (0.11 Markdown memory) hides the Memory tab; the health card should get a place in the files view.
- The `profile/<space>.md` cache read by coding hooks is refreshed by the nightly consolidation, not on every edit.
- Measure precision of the move and wrong checks on a synthetic eval set before turning on any auto-apply.
