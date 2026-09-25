# 33 — Search expands queries with the glossary

**What to build:** Searching for "CNH" also finds memories that say "carteira de motorista" or "permis de conduire", because
active terms add their synonyms to the query. Related terms only nudge the ranking.

- `MemoryService.search` loads the workspace's active terms (a small set, held in memory for the call), finds the
  ones whose `term` or one of whose aliases occurs in the query, and appends their other aliases and their
  expansion to the query text before it is embedded.
- Terms in `related` only add a small rank boost to rows that mention them, and never add text to the query.
- Only `active` terms count. A `candidate` term expands nothing.
- Results report which terms expanded the query, so the effect can be seen.
- `memhub search` shows the expansion; nothing changes when the workspace has no terms.

**Blocked by:** 32 — Terms from explicit definitions

**Status:** done

- [ ] AC 27: with an active term `CNH ↔ carteira de motorista ↔ permis de conduire`, a query "como trocar a CNH" finds a memory that only says "carteira de motorista" (checked with fake embeddings), and the result names the term that expanded it
- [ ] A candidate (unapproved) term expands nothing
- [ ] `related` terms boost rank but add nothing to the query text
- [ ] A workspace with no terms searches exactly as before
