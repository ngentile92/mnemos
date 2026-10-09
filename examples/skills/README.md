# Example skills repo

A starting point for your **private** skills repo (`<you>/mnemos-skills`). Skills use the
[Agent Skills](https://agentskills.io) format: one folder with a `SKILL.md`. The hub's `skills-sync` container runs
`git pull` every 60 s, so a push shows up in every assistant within about a minute.

```bash
mkdir mnemos-skills && cp -R mnemos/examples/skills/. mnemos-skills/ && cd mnemos-skills
git init && git add . && git commit -m "Initial skills"
gh repo create <you>/mnemos-skills --private --source . --remote origin --push
```

## Layout and visibility

```
skills/
  shared/    → visible in every context
  work/      → only in the work connector
  personal/  → only in personal
  side/      → only in side
```

One folder per context in your `config/contexts.yaml` (plus `shared/`). A context's skill can be shared with others
by adding `metadata.hub-share: "side"` (space-separated list).

Included examples (all in `shared/`): `review-pr`, `debug-error`, `status-report`, `technical-docs`.

## Writing a skill

```markdown
---
name: kebab-case-name               # same as the folder name
description: What it does and WHEN to use it (this is what the model reads to decide).
metadata:
  hub-owner: personal               # optional; if present it must match the folder
  hub-share: "side"                 # optional
---

# Instructions…
```

Supporting files (`reference.md`, templates) can live in the same folder; `skills_get` reads them.

## Rules

- **Never** put secrets here: secrets live in Infisical and the hub injects them. `validate.py` rejects token patterns.
- No symlinks, files <= 256 KiB, unique names across the repo.
- Validate before pushing: `pip install pyyaml && python scripts/validate.py` (set `SKILLS_CONTEXTS` if your
  contexts differ from `work personal side`).
- The hub only needs **read** access: add its deploy key without write permission.
- gbrain-format skills (`triggers`, `tools`, `mutating`…) and skillpack files are accepted: see `docs/skills-gbrain.md` in the Mnemos repo.
