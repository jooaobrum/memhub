# 10 — Reconcile: duplicate merge, conflict detection, preference replacement

**What to build:** Repeated or contradicting knowledge from new conversations updates the ledger safely and never deletes anything automatically. For each admitted candidate, search the top 5 active memories (plus pending candidates) of the same type, scope and owner:

- **Similarity ≥ `reconcile.duplicate`:** merge. The candidate's evidence is appended to the existing memory. `seen_count += 1` only if the source `thread_id` is new for that memory. No new row is created.
- **Similarity within `conflict_band`, and either sharing an entity or with no entities on either side:** one `llm.judge` call returns `same | conflicts | unrelated`. `same` merges. `conflicts` creates a `candidate` with `conflicts_with = <existing memory_id>`. `unrelated` creates a new memory.
- **Otherwise:** a new memory.
- **Preferences:** a `Preference` replaces the active preference with the same `key` for that user, as a new version (the old one becomes `superseded`).
- The run summary gains a `merged` count. The judge is called at most once per candidate in the conflict band.

**Blocked by:** 09 — Admission score; 05 — Workspace review queue: approve and reject

**Status:** done

- [ ] AC 5: a candidate ≥ `duplicate` similar to an active memory of the same type and scope appends its evidence instead of creating a row, and `seen_count` increments only for a new source thread
- [ ] A conflict judged by the (fake) judge becomes a candidate with `conflicts_with`, and the existing memory is untouched
- [ ] The judge is not called outside the conflict band, and is called at most once per candidate inside it
- [ ] A new `Preference` with an existing `key` supersedes the old one, so there is one active preference per (user, key)
- [ ] A conflict candidate shows up in `memhub queue` and can be resolved with `approve --resolve`
