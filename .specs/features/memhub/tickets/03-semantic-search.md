# 03 — Semantic search with citable results

**What to build:** `memhub search "<query>" [--user ..] [--ws ..]`, backed by `MemoryService.search`, returns the most similar memories that the caller is allowed to see. Each result carries what an agent needs to cite it.

- Only `active` rows are searchable. Results include `memory_id`, `version`, `type`, `verified`, `content` and the evidence sources.
- The visible set is the workspace-scope memories of the workspace plus the user-scope memories of that user. Other users' memories are never returned.
- The service API takes `k`, an optional type filter and an optional entity boost (memories that share an entity with the query context rank higher). The CLI exposes query, user and workspace.

**Blocked by:** 02 — Add memories by hand and list them

**Status:** done

- [ ] AC 7 (search half): a user-scope memory with `verified=false` is returned marked `verified=false`
- [ ] Candidate, rejected, archived and superseded rows are never returned
- [ ] One user's memories are never returned for another user
- [ ] Results expose `memory_id`, `version`, `verified` and the evidence sources
- [ ] An entity boost changes the ranking in a test with fake embeddings
