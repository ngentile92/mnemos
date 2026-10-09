---
name: incident-postmortem
description: Writes a blameless incident postmortem (timeline, impact, root cause, contributing factors, action items) from logs, chat history or notes. Use after an outage, a failed deploy or a production bug, or when asked for an "incident report" or "postmortem".
metadata:
  hub-owner: work
  version: "1"
---

# Incident postmortem

## Gather
- `memory_search` for previous incidents on the same system (repeat causes matter).
- Facts only from what the user provides (logs, alerts, messages, commits). Times with time zone. Mark guesses as
  `unconfirmed`.

## Structure
```
# Postmortem: <title> (<date>)
**Severity / duration / impact:** who and what was affected, for how long, how it was measured
## Timeline   (detection → mitigation → resolution, one line per event)
## Root cause (the technical chain; "5 whys" if useful)
## Contributing factors (missing alerts, tests, docs…)
## What went well
## Action items (- [ ] action — owner — due date; each one prevents, detects or mitigates)
```

## Rules
- Blameless: describe systems and decisions, not people's faults.
- Never paste secrets, tokens or customer personal data from logs; redact them.
- At the end, `memory_save` one note: incident, root cause in one line, link to the doc.
