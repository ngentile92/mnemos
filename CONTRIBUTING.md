# Contributing

Thanks for your interest! Mnemos is a personal project shared as-is, but issues and pull requests are welcome.

## Before you start

- Open an issue first for anything larger than a small fix, so we can agree on the approach.
- Keep the security model intact (see [`SECURITY.md`](SECURITY.md)): no tools that run commands, no secret values
  reaching the model, no new public surface.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e "gateway[dev]"
.venv/bin/pytest -q gateway/tests                     # unit tests (no Docker needed)
python examples/skills/scripts/validate.py examples/skills
shellcheck -S warning scripts/*.sh skills-sync/sync.sh
docker compose -f compose.dev.yaml up -d --build      # optional: full dev stack + scripts/smoke_test.py --dev
```

## Pull requests

- One topic per PR, with tests for new behavior.
- **Never commit real data**: no `.env`, no `config/contexts.yaml` / `secret-policy.yaml` / `cognee-datasets.json`,
  no real hostnames, tailnet names, emails or memory contents in tests or docs. Use neutral examples
  (`example.com`, `tailXXXX`, `example-org/repo`). CI runs gitleaks on the full history.
- Server instructions must stay under 1,300 characters (there is a test).
- New docs in English are preferred; Spanish docs are welcome too.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
