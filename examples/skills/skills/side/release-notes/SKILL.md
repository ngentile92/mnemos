---
name: release-notes
version: 1.0.0
description: Writes user-facing release notes and a changelog entry from merged PRs or commits since the last release. Use when asked for "release notes", a "changelog" or "what changed since vX".
triggers:
  - "release notes"
  - "changelog"
  - "what changed since"
tools: [memory_search, memory_save]
mutating: false
metadata:
  hub-owner: side
---

# Release notes

1. Get the change list (merged PRs or `git log <last-tag>..HEAD`). Ask for it if you can't read the repo.
2. `memory_search` for the project's past releases to keep the same style and version scheme.
3. Group by impact for users, not by commit: **New**, **Improved**, **Fixed**, **Breaking / action needed**.
   One line each, plain language, no internal ticket jargon; link the PR.
4. Breaking changes first, with what the user must do. Skip pure refactors and CI changes unless they matter.
5. Suggest the next version (semver) and a `CHANGELOG.md` entry. Pass `project` when saving a note with the
   release date and version.
