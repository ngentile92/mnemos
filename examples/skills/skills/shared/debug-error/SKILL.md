---
name: debug-error
description: Systematic debugging of an error or bug - reproduce, read logs and error tracking, form hypotheses, minimal fix, regression test, and save the lesson to memory. Use when given a stack trace, an error message, a failing test, an error-tracker alert, or "this doesn't work".
metadata:
  hub-owner: shared
  version: "1"
---

# Debug an error

## 0. Context first
- `memory_search` with the error message, module or symptom: it may have been solved before.
- Identify the repo and its stack from the code itself (`pyproject.toml`, `package.json`, `compose.yaml`, CI config).
  Don't assume a framework you haven't seen in the repo.

## 1. Reproduce
- Write down the exact failing command and expected vs actual result.
- Shrink the case: one test, one `curl`, one script. No repro, no fix: if you can't reproduce, say what is missing
  (data, env, version).

## 2. Evidence
- Full stack trace: find the first frame in *our* code, not the library's.
- Logs: `docker compose logs <service> --since 30m`, the hosting platform's logs, or the repo's `logs/`.
- **Error tracking** (Sentry or similar, if the project uses it and a connector is available): find the issue, look at
  events, tags (release, environment), breadcrumbs and when it started.
- What changed: `git log --since="3 days ago" --oneline`, `git diff`, dependency upgrades, environment variables
  (names only, never values).

## 3. Hypotheses
- List 1-3 hypotheses ordered by likelihood, each with the evidence that would confirm or rule it out.
- Test one at a time with the cheapest experiment (log line, breakpoint, test). Note what you ruled out.

## 4. Minimal fix
- The smallest change that addresses the root cause, not the symptom. No opportunistic refactors in the same change.
- If the fix is a workaround, say so and leave a TODO pointing to the issue.

## 5. Regression test
- A test that fails without the fix and passes with it (`pytest path/to/test.py -k <name>`, `npm test`, ...).
- Run the repo's linters/type checkers (`ruff`, `mypy`, `npm run lint`, `tsc`, whatever the repo uses).

## 6. Wrap-up
- Summary: root cause, fix (file:line), test added, residual risk.
- If it came from an error tracker, suggest resolving the issue with the fix's release; don't change it unless asked.
- `memory_save`: "Bug <symptom> in <repo> (<date>): cause <X>, fix <Y>, how to detect it". Pass `project` where required.
- If you need to call an API with credentials to reproduce, use `secrets_list` + `secret_http_request`; never ask for
  or paste a key's value.
