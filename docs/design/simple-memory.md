# Simple memory (0.11+)

Status: accepted. Rolls out over 0.11 (files + tools + migration + UI), 0.12 (native sync with
coding agents), 0.13 (hub and cloud apps on the same files).

## Why

Memory had grown into several stores (notes in SQLite, a ledger, a model with entities and a
graph, an optional Cognee index) and around twenty tools. Users could not see or fix what an
assistant "knew", and assistants had to pick between overlapping tools. The simple design makes
the memory a handful of Markdown files that a person can read and edit, with git for history.

## The canon: Markdown files per space

```
<home>/memory/                 a git repository (pure Python, dulwich)
  <space>/
    profile.md                 short, always loaded at the start of a chat (≤ 40 bullets)
    topics/
      people.md  projects.md  goals.md  how-i-work.md  references.md  notes.md  <any>.md
  .mnemos/index.json           derived: note ids and metadata (rebuilt from the files)
  .mnemos/active               present when this home uses the files store
```

* A note is a bullet (`- text`). Continuation lines are indented two spaces.
* Bullets under a heading named "No longer true" are history: kept for context, never returned
  as current facts. Superseding a note moves it there as `- ~~old~~ (replaced YYYY-MM-DD)`.
* Optional YAML front matter (`title`, `tags`) at the top of a file.
* Every change is one git commit, authored by the app that made it (`Claude <claude@mnemos.local>`);
  edits made by hand in an editor are picked up on the next read and committed as "you".
  Undo = restore an earlier version, which is itself a new commit.

## Tools (one connector, per-space permissions unchanged)

| Tool | Does |
|---|---|
| `memory_start` | profile + list of topics for the space; call once at the start |
| `memory_read` | read a topic file (or the profile) |
| `memory_search` | search across the files of the allowed spaces |
| `memory_remember` | add a bullet; routed to the right file by kind/tag, or to an explicit topic |
| `memory_edit` | replace a note's text, or supersede it (old one moves to "No longer true") |
| `memory_forget` | delete a note (git history keeps it) |

`hub_whoami`, `hub_use_context` and the skills tools stay. In files mode the older note/ledger
tools are no longer exposed to assistants; the local UI still uses them in-process.

The guardrails (secrets, credentials, sensitive data) apply to `memory_remember` exactly as before.

## Routing

1. explicit `topic` argument; 2. a `topic-<name>` tag; 3. a `[[Link]]` to an existing topic;
4. `pinned` → profile; 5. kind (identity/preference/habit → profile, person/relationship →
people, goal → goals, project state/decision → projects, skill/instruction/feedback →
how-i-work, reference → references); 6. otherwise `topics/notes.md`. When the profile is full,
new profile notes go to `topics/about-me.md`.

## Migration (existing homes)

`preview` writes the would-be files to `<home>/migration-preview/` plus a report and changes
nothing. `apply` writes `memory/`, keeps the same note ids (so search, the ledger and links keep
working), makes one commit and turns the files store on. `undo` turns it off again; the old
SQLite store is never modified by the migration. New homes start on files.

## Labs (optional, maintained)

Entities, the graph, model-built pages, the "still true?" check and the nightly consolidation are
**Labs**: off by default, kept working and tested, turned on per home in Settings → Labs (or with
`MNEMOS_LABS=1|0`, which wins). Cognee stays an optional search index on the hub, off by default.
Neither is required for memory to work.

## Native sync (0.12, opt-in)

Coding agents read memory natively from files they already load: Claude Code via
`~/.claude/rules/mnemos.md`, Codex via `~/.codex/AGENTS.md` (a delimited block), Cursor via a
user rule file. Mnemos writes only its own delimited block, keeps a backup of every file it
touches, and only for spaces the user picked. Edits made in those files are not read back;
memory is changed through the tools or the app.

## Non-goals

No vector database required, no new server, no lock-in: the files are plain Markdown and the
history is plain git.
