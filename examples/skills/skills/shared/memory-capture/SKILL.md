---
name: memory-capture
version: 1.0.0
description: Decides what is worth remembering from the conversation and saves it as short, self-contained notes with [[links]] to people, companies and projects. Use when the user says "remember this", "save this", "note that", or at the end of a session with decisions worth keeping.
triggers:
  - "remember this"
  - "save this"
  - "note that"
tools: [memory_search, memory_save, memory_update]
mutating: true
metadata:
  hub-owner: shared
---

# Memory capture

## What to save
- Decisions (and why), preferences, facts about people/companies/projects, commitments with dates.
- Not: chit-chat, things already in the code or docs, anything secret (tokens, passwords, `.env` values).

## Before saving
1. `memory_search` for the topic. If a note already says it, **don't duplicate**: if it changed, fix the old note
   with `memory_update` (its previous version stays in history) instead of adding a contradicting one.
2. One fact per note, readable on its own a year from now: include the date and who/what it is about.

## How to write it
- 1-3 sentences. Start with the subject: "[[Ana López]] prefers async updates on Fridays (2026-03-02)."
- Wrap people, companies and projects in `[[double brackets]]` with the same spelling every time: Mnemos turns
  them into links and `memory_entity` builds a page per name.
- Add `tags` for the kind of note (`decision`, `preference`, `contact`, `todo`). Pass `project` where required.

## After saving
Tell the user in one line what was saved, so they can correct it.
