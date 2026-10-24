"""Three starter skills for a new memory (FTUE #197): short, generic, and each with a sentence to try it.

Written to <home>/skills/shared/<name>/SKILL.md when a home is created, or from Skills → "Add 3 examples". Never
overwrites a skill that already exists. `metadata.hub-try` is the sentence the app copies for "Try it in your AI".
"""

from __future__ import annotations

from pathlib import Path

STARTERS: dict[str, str] = {
    "email-reply": """---
name: email-reply
description: Writes a reply to an email or message in the user's own style (length, tone, greeting and sign-off from memory). Use when the user pastes a message and asks for a reply.
metadata:
  hub-owner: shared
  hub-try: "Use my email-reply skill: reply to this saying I can meet next Tuesday afternoon. 'Hi! Do you have time for a quick call next week?'"
---

# Email reply

1. `memory_search` for "writing style", "how I write emails" and the sender's name, to reuse tone and context.
2. Reply in the language of the message, short (3-6 sentences) unless the user asks for more.
3. Match the user's usual greeting and sign-off if memory has them; otherwise keep them simple.
4. Show the reply ready to copy, then one line: "Want it shorter, warmer or more formal?"
5. If the user corrects the style ("I never say 'Dear'"), save that preference with the memory tool.
""",
    "meeting-summary": """---
name: meeting-summary
description: Turns meeting notes or a transcript into a short summary with decisions and next steps, and saves the lasting decisions to memory. Use when the user pastes notes from a meeting or call.
metadata:
  hub-owner: shared
  hub-try: "Use my meeting-summary skill on these notes: 'Talked with Sam about the website. Launch moves to May 10. Sam sends the new texts Friday. Still unsure about the price page.'"
---

# Meeting summary

1. Write:
   - **Summary:** 2-4 bullets
   - **Decisions:** one bullet each, with the reason if stated
   - **Next steps:** task, who, when (write "no date" if missing)
   - **Open questions**
2. Save each lasting decision to memory in one sentence (for example "The website launch moved to May 10.").
3. Do not invent owners or dates.
""",
    "weekly-plan": """---
name: weekly-plan
description: Helps the user plan their week from what memory knows (goals, projects, routines) and what they say now. Use when the user asks to plan the week or says they have too much to do.
metadata:
  hub-owner: shared
  hub-try: "Use my weekly-plan skill: help me plan this week."
---

# Weekly plan

1. Start with what memory knows: `memory_search` for goals, current projects and routines.
2. Ask at most one question if something essential is missing (for example fixed appointments).
3. Propose: the 3 most important things this week, then a simple day-by-day list. Keep it realistic.
4. If the user states a new goal or routine, save it to memory in one sentence.
""",
}


def install(home: Path) -> list[str]:
    """Write the starter skills that are not there yet. Returns the names written."""
    base = Path(home) / "skills" / "shared"
    made = []
    for name, text in STARTERS.items():
        f = base / name / "SKILL.md"
        if f.exists():
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        made.append(name)
    return made
