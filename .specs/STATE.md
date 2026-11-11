# Project state

## Decisions

| ID | Decision | Rationale | Status | Date |
| --- | --- | --- | --- | --- |
| AD-001 | Every `Memory` method, reads and review included, takes `workspace_id`, `user_id`, `run_id` (a `CallContext`); `scope(...)` binds them once | asked: "IDs required on every method, or only on writes and search?" - user chose "Required on every method"; prevents cross-user reads by id (PRD D6) | active | 2026-11-11 |
| AD-002 | `Memory(...)` runs no SQL; tables are created only by the idempotent `m.init()` / `memhub init` | asked: "Should Memory(...) create the tables itself, or should the app call m.init()?" - user chose "Explicit m.init()"; no DDL on a borrowed app connection | active | 2026-11-11 |
| AD-003 | A memory-writing tool returns `{"id": <memory_id>, "event", "status"}` to the model, in `MemoryToolkit` and `MemoryMiddleware` alike | asked: "Keep today's tool output, or switch to {id,event,status}?" - user chose "Switch to {id,event,status}"; one write-result vocabulary everywhere (PRD R17) | active | 2026-11-11 |
| AD-004 | memhub never closes a connection or pool it did not open, and commits or rolls back only its own transaction (a savepoint inside the app's) | PRD R2 / TDD "Connections passed in": the app owns pooling, SSL and token refresh | active | 2026-11-11 |
| AD-005 | `Settings.database_url`, `llm` and `embeddings` are optional; with no config, `Memory` uses built-in defaults | asked: "What should Memory(db=, llm=, embedder=) need in PR 1?" - user chose "Defaults, yaml optional"; pulls this slice of PR 2 forward | active | 2026-11-11 |
