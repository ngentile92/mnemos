# Simple memory on the Docker hub (preview, 0.13)

The hub can keep a context's memory in the same Markdown files + git layout as the local app
([design](design/simple-memory.md)). It is **off** unless you turn it on per context; Cognee is never modified and can
stay on as an optional search index.

## Try it on one context

1. Preview inside that context's gateway container (writes `<memory>.preview/<context>/` and a report, changes nothing):

   ```bash
   docker compose exec gateway-personal python -m hub_gateway.hub_files preview
   ```

2. Apply (writes that context's folders into the shared memory repo, keeping every note id):

   ```bash
   docker compose exec gateway-personal python -m hub_gateway.hub_files apply
   ```

3. Set `HUB_MEMORY_STORE=files` for that gateway and restart it. The memory lives in `HUB_MEMORY_DIR`
   (default: a `memory` folder next to the contexts' data dirs, one git repo for all contexts).

**Undo:** remove `HUB_MEMORY_STORE` and restart; the context reads Cognee again exactly as before.

With files on, assistants get `memory_start`, `memory_read`, `memory_search`, `memory_remember`, `memory_edit` and
`memory_forget`; the older tools stay available to the dashboard. Per-context permissions and bridges are unchanged.
