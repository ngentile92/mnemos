# What Mnemos isolates, what leaves your machine, and what it does not protect

An honest map of the trust boundaries. Threat model and mitigations: [`SECURITY.md`](../SECURITY.md).

## What leaves your machine

| Data | Goes to | When |
|---|---|---|
| Search results (`memory_search`, `memory_list`, `memory_history`) and skill text | The AI provider of the assistant that called the tool (Anthropic, OpenAI, xAI, Cursor…) | Every call: they become part of that conversation |
| Text you save (`memory_save`, `memory_update`) | The same AI provider (it wrote it) and the **extraction LLM** set in `cognee.env` | On save/correct. With Ollama the extraction stays on your machine; with OpenAI the note text is sent to OpenAI |
| API responses fetched with `secret_http_request` | The AI provider (redacted of the secret value) | Every call |
| Secret values | **Nowhere**: injected server-side, only to allow-listed hosts | — |
| Backups | Your restic repository and, if configured, your own cloud drive (encrypted) | On schedule |

## What is isolated between contexts

- **Memory**: each context has its own Cognee user, API key and datasets; Cognee enforces the permissions,
  and the gateway filters again. A context can read and add to `shared`, never another context.
- **Secrets**: one Infisical identity per context, read-only (Viewer), limited to that context's secrets.
- **Skills**: each gateway only sees its own folder plus `shared/`.
- **Access**: one public hostname and one GitHub OAuth app per context, each with its own login allow-list.
- **Edit history** (`ledger.sqlite`): one file per gateway, in that context's data volume.

## What is NOT isolated

- **`shared`** is readable by every context by design. Do not put work secrets or private life details there.
- **One host, one owner.** All contexts run on the same machine, same Docker, same Cognee and Infisical
  instances. Anyone with access to the host (or to `.env`) can read everything. Mnemos is single-user.
- **The AI providers.** If you use the same assistant account for work and personal contexts, that provider
  sees both conversations. Mnemos isolates what each *hub* returns, not what a provider remembers.
- **Source app names** shown in provenance are what the MCP client declares; they are informative, not proof.
- **Prompt injection.** Saved text and skills are marked as data, not instructions, but a model can still be
  fooled by them. Review what you save and what goes into your skills repo.
- **Deletion is per note.** Removing a note removes what the graph derived only from it; the same fact
  stated in another note survives. Removed notes keep a copy in the edit history (for undo) until you
  delete `ledger.sqlite` rows yourself; backups keep older copies according to your retention.
