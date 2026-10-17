# Publishing mnemos-hub (PyPI + npm)

`.github/workflows/publish.yml` runs on every published GitHub Release. It always builds and checks the packages.
It uploads only when the repo variable `PUBLISH_ENABLED` is `true`, using trusted publishing (OIDC): no tokens.

One-time setup (maintainer):
1. **PyPI** → https://pypi.org/manage/account/publishing/ → *Add a new pending publisher*: project `mnemos-hub`,
   owner `ngentile92`, repo `mnemos`, workflow `publish.yml`, environment `pypi`.
2. **npm** → create/log into an npm account. The first publish of a new name must be manual
   (`cd packaging/npm && npm publish --access public`); then on npmjs.com → package `mnemos-hub` → *Settings →
   Trusted publishing* → GitHub Actions, repo `ngentile92/mnemos`, workflow `publish.yml`, environment `npm`.
3. GitHub → repo *Settings → Environments*: create `pypi` and `npm` (optionally require approval);
   *Settings → Variables → Actions*: `PUBLISH_ENABLED = true`.
4. Re-run: *Actions → publish → Run workflow* (or publish the next release).

Release checklist: bump `gateway/pyproject.toml`, `packaging/npm/package.json`, `packaging/mcpb/manifest.json`,
`desktop/src-tauri/tauri.conf.json` to the same version, merge, tag `vX.Y.Z` (release.yml builds the app).
