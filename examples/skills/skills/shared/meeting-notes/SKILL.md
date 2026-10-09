---
name: meeting-notes
description: Turns a meeting transcript or rough notes into a summary with decisions, action items (owner + date) and open questions, and saves the durable parts to memory. Use when the user pastes a transcript, call notes or says "summarise this meeting".
metadata:
  hub-owner: shared
  version: "1"
---

# Meeting notes

1. `hub_whoami` (context) and `memory_search` for the meeting's topic or attendees, to reuse names and past decisions.
2. Write:
   ```
   ## <meeting> — <date>
   **Attendees:** …
   **Summary:** 3-5 bullets
   **Decisions:** one bullet each, with the reason if stated
   **Action items:** - [ ] <task> — <owner> — <due date or "no date">
   **Open questions:** …
   ```
   Only what was said: if an owner or date is missing, write "unassigned" / "no date"; don't invent.
3. Save to memory (one `memory_save` per item, short and self-contained, with `[[Person]]` / `[[Project]]` links):
   each decision and each action item with an owner. Not the whole transcript.
4. If the user wants the action items in a task tracker, offer it; don't create tasks without being asked.
