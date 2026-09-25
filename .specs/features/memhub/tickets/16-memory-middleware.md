# 16 — `MemoryMiddleware` for LangChain agents

**What to build:** Any LangChain agent can add long-term memory by attaching `MemoryMiddleware(service, workspace_from=..., user_from=...)`, which is a LangChain `AgentMiddleware`. It is library-only and is not wired into Habitantes.

- **First turn of a thread:** load the `retrieval: always` types (the preference profile, capped at `max_chars`) and the skill index (name + description). Cache them in agent state as a snapshot that stays fixed for the whole thread.
- **Every user turn:** run `service.search(query=last user message, k, entity boost)` and inject a `<memory>` block with items formatted as `[id|type|verified] content (source)`.
- **Tools:**
  - `search_memory(query, type?)`
  - `load_skill(name)`
  - `propose_memory(type, fields, evidence)`: always creates a `candidate`, even for user scope, and passes the injection check. `/remember` uses it.

**Blocked by:** 03 — Semantic search with citable results; 04 — Versioned edit, archive and delete by id

**Status:** done

- [ ] Unit tests against a fake agent show that the profile and skill index load once per thread and do not change mid-thread, even if the ledger changes
- [ ] Exactly one search per user turn, and the `<memory>` block contains ids, types and the verified flag
- [ ] The profile is truncated at `max_chars`
- [ ] `propose_memory` creates only candidates, and injection-pattern content is refused
- [ ] `load_skill` returns the skill body by name
