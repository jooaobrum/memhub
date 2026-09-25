# 02 — Add memories by hand and list them

**What to build:** A reviewer can run `memhub add --type <t> --scope <s> [--ws ..] [--user ..] --file x.json [--reference WO-123]` to add a memory from a JSON file, then see it with `memhub list [--status ..] [--type ..] [--user ..] [--ws ..]`. This is the first end-to-end write path through `MemoryService` into the ledger. Later slices reuse it.

- The input is validated against the registered type's Pydantic schema. On failure, the validation errors are printed and nothing is written.
- The content passes the prompt-injection pattern check before it is stored.
- The row is stored as version 1 with a new `memory_id`, the current `schema_version`, and its embedding of `content`. Evidence is `{source: "manual", actor, reference?}`. `verified=true` and `created_by=<actor>`.
- Scope must be one of the configured `scopes`. `user_id` is required when the scope is `user`. `workspace_id` defaults to `workspace_default`.
- A manually added user-scope memory is `active`. A manually added workspace-scope memory by a `workspace_admin` is also `active`.

**Blocked by:** 01 — Package skeleton, config loader, type registry and `memhub init`

**Status:** done

- [ ] AC 9: fields that fail the type's validation are rejected with the validation errors, and no row is written
- [ ] Content that matches an injection pattern is rejected, and no row is written
- [ ] A valid add writes exactly one row with the correct version, status, verified flag, evidence and a non-null embedding
- [ ] `memhub list` filters by status, type, user and workspace
- [ ] Embeddings come from a fake embedder in tests, so there are no network calls
