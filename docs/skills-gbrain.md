# Using gbrain skills in Mnemos

Mnemos skills follow the Agent Skills format (`SKILL.md` with `name` + `description`). Mnemos also
accepts skills written in [gbrain](https://github.com/garrytan/gbrain)'s format, so a gbrain skill or
skillpack can be dropped into your skills repo unchanged.

## What is accepted

```yaml
---
name: query                 # must match the folder, kebab-case (same rule as gbrain)
version: 1.0.0              # optional, shown in skills_list
description: |              # multi-line is fine: whitespace is collapsed
  Answer questions using the brain's knowledge...
triggers:                   # optional list of phrases; shown in skills_list for routing
  - "what do we know about"
tools: [search, get_page]   # optional; skills_get maps them to Mnemos tools
mutating: false             # optional bool, shown in skills_list
writes_pages: false         # any other gbrain key is ignored
---
```

- `triggers` and `tools` must be lists of strings (max 50 triggers) and `mutating` a boolean; otherwise
  the skill is reported as invalid and not served (the same happens with the validator).
- Files next to `SKILL.md` (`routing-eval.jsonl`, scripts, references) are readable with
  `skills_get(name, file=...)`, as with any skill.
- Skillpack support files are allowed in an owner folder: `RESOLVER.md`, `_*.md` files and the
  `conventions/`, `migrations/` and `_*/` folders. They are not skills; the validator does not require a
  `SKILL.md` in them.

## Where to put them

Mnemos keeps its per-context layout: `skills/<owner>/<name>/SKILL.md`, owner = `shared` or a context.
To use a gbrain pack in every context, copy its `skills/*` into `skills/shared/`; for one context,
into `skills/<context>/`. Check it with `python examples/skills/scripts/validate.py <your-skills-repo>`.
(Tested with the 75 skills shipped in gbrain's repo: all load.)

## Tools

gbrain skills name gbrain tools. `skills_get` returns `tool_equivalents` with the closest Mnemos tool:

| gbrain | Mnemos |
|---|---|
| `recall`, `search`, `query`, `get_backlinks`, `traverse_graph`, `get_timeline` | `memory_search` |
| `get_page`, `list_pages` | `memory_list` |
| `put_page`, `add_timeline_entry`, `put_raw_data` | `memory_save` |
| anything else (`exec`, `shell`, jobs, schema, web…) | none (`null`): the skill step needs another tool or is skipped |

Mnemos has no tools that run commands, so skills relying on `exec`/`shell` only work partly.
Skills are data, not instructions to the hub: review a pack before adding it, as with any skill.
