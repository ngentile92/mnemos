---
name: status-report
description: Builds a status report (weekly or for a period) by combining git history, the task tracker and hub memory; returns done / in progress / blockers / next steps. Use when asked for a status report, a weekly, "what did I do this week", a standup summary or an update for a manager.
metadata:
  hub-owner: shared
  version: "1"
---

# Status report

## 1. Scope
Confirm the period (default: last 7 days, Monday to today), the context (`hub_whoami`) and the audience (manager,
team, yourself). Write in the language the audience uses.

## 2. Sources (in this order)
1. **Memory**: `memory_search` "progress / decisions / blockers <period>"; in contexts with projects, filter by `project`.
2. **Git** (if you have a shell on the user's machine): the user's repos for this context. Ask where they live if
   memory doesn't say.
   ```bash
   for d in ~/code/*/; do
     [ -d "$d/.git" ] || continue
     out=$(git -C "$d" log --all --since="7 days ago" --author="$(git config user.email)" \
           --no-merges --format='%cs %h %s')
     [ -n "$out" ] && printf '## %s\n%s\n' "$(basename "$d")" "$out"
   done
   ```
   With `gh`, add PRs: `gh pr list --author @me --state all --search "updated:>=YYYY-MM-DD"`.
3. **Task tracker** (Jira, Linear, ClickUp, GitHub Issues... if a connector is available): tasks assigned to the user
   and updated in the period: closed, in progress, overdue.
4. If a source is unavailable, say so in the report; don't fill it in from memory.

## 3. Format
```
# Status <period> — <context>
**TL;DR:** 2-3 lines with what matters most.

## Done
- <outcome, not activity> (repo/PR or task)
## In progress
- <what> — <% or next milestone> — ETA
## Blockers / risks
- <blocker> — what is needed and from whom
## Next steps
- …
```
- Group commits by theme/feature; don't list individual commits. Outcome > activity ("ingestion is stable for the
  pilot", not "commits in repo X").
- At most ~15 bullets. Link PRs and tasks when they exist.
- Anything without evidence (no commit, task or note) goes in as "to confirm".

## 4. Wrap-up
- Don't send it by chat or email until the user approves the final text.
- `memory_save` a short note: "Status <date>: done X, in progress Y, blockers Z" (right context; `project` where required).
