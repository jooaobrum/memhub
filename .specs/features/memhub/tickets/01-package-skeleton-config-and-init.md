# 01 — Package skeleton, config loader, type registry and `memhub init`

**What to build:** An engineer can install the `memhub` package, write a `memhub.yaml`, and run `memhub init` against a Postgres + pgvector instance. This creates the extension and the two ledger tables (`<prefix>_memory`, `<prefix>_memory_runs`) with the prefix and embedding dimensions from config. It also saves the embedding model and dims. Every later command refuses to run if the config's embedding model or dims no longer match what was saved.

This ticket lays the foundation the other slices build on:
- Config loading uses pydantic-settings with `${ENV}` interpolation. Model entries (`{provider, model, base_url?, api_key_env, params?}`) are built into LangChain objects with `init_chat_model` / `init_embeddings`. A configured chat model that does not support `with_structured_output` fails fast.
- The type registry holds `MemoryBase`, `EntityRef` and the built-in types `Fact`, `Preference`, `Episode` and `Skill`. Project types are registered as `module:Class`, keyed by `(type_name, schema_version)`. A stored payload is validated with the class for its own version. `EntityRef.type` must be one of the configured `entity_types`.
- A pytest fixture starts `pgvector/pgvector:pg16` and skips when Docker is not available.
- The CLI entry point (typer) acts as `actor=cli:<os user>` with roles from `MEMHUB_CLI_ROLES` (default `workspace_admin`).
- Optional extras: `memhub[openai]` and `memhub[anthropic]`.

**Blocked by:** None — can start immediately.

**Status:** done

- [ ] `memhub init` creates the pgvector extension, both tables and all indexes (including the partial unique index "one active version per `memory_id`" and the HNSW index), and running it twice is a no-op
- [ ] Table names use `project_prefix`, and the `vector(dims)` column uses `embeddings.dims`
- [ ] When the embedding model or dims in config differ from what `init` saved, commands exit with a clear error that points to `memhub reembed`
- [ ] Unit tests cover: `${ENV}` interpolation, building models from config for the openai/OpenRouter and anthropic providers (without network calls), registry lookup by `(type, schema_version)`, and rejection of an unknown entity type
- [ ] The ledger test fixture runs against a real pgvector container and is skipped cleanly when Docker is absent
