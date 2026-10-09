---
name: weekly-review
description: Guides a short personal weekly review (what got done, what slipped, priorities for next week) using what memory already knows, and saves the outcome. Use when the user says "weekly review", "plan my week" or "how did my week go".
metadata:
  hub-owner: personal
  version: "1"
---

# Weekly review

1. `memory_search` for notes from the last 7 days (commitments, todos, decisions) and last week's review
   (`tags: weekly-review`).
2. Ask, one at a time and briefly: what went well, what slipped and why, anything to drop.
3. Propose at most 3 priorities for next week, tied to the user's stated goals. Flag commitments from memory
   that have a date this week.
4. Save one note: "Weekly review <ISO week>: done …; slipped …; priorities …" with `tags: ["weekly-review"]`.
5. Keep it under 10 minutes: no long lists, no lectures.
