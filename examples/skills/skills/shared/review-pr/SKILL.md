---
name: review-pr
description: Reviews a pull request with a fixed checklist (correctness, security, tests, readability) and returns prioritized findings. Use when asked to review a PR, a diff or a code change.
metadata:
  hub-owner: shared
  version: "1"
---

# Review a PR

## When to use it
When the user shares a PR link, a diff, or says "review this change".

## Steps
1. Read the PR description and summarize in one line what it is trying to do.
2. Walk the diff file by file. For each finding note file, line and severity.
3. Checklist:
   - **Correctness**: edge cases, unhandled errors, race conditions.
   - **Security**: secrets in code, unvalidated input, excessive permissions.
   - **Tests**: are there tests for the new behavior? Do they cover the error path?
   - **Readability**: naming, functions that are too long, comments that lie.
4. Don't propose style changes a linter would already fix.

## Output format
- One verdict line: `Approve`, `Approve with minor changes` or `Request changes`.
- Findings ordered by severity (`blocking` → `important` → `minor`), at most 10.
- If you find nothing important, say so explicitly instead of inventing issues.
