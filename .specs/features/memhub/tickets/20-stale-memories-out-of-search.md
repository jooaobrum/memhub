# 20 — Stale memories are kept but left out of search

**What to build:** Once a memory's `valid_until` has passed, the agent no longer sees it, but it is still in
the ledger and can be looked up. Nothing is archived or deleted, and no background job runs.

- Stale is computed from the date: `status = active AND valid_until < now`. There is no new status.
- By default the service's search excludes stale memories. An `include_stale` option brings them back. The
  service takes `now` as a parameter, so tests control the clock.
- `memhub search ... --include-stale` and `memhub list --stale` (only stale ones).
- The `list` output marks stale rows.
- Search results include `valid_until`.
- Reconcile still sees stale memories, so a claim repeated after its memory expired renews that memory (ticket
  19's merge rule) instead of creating a duplicate.

**Blocked by:** 19 — Validity window

**Status:** done

- [x] AC 14: a memory with `valid_until` in the past is not returned by search unless `include_stale` is set, and its `status` stays `active`
- [x] With `now` set 31 days after a `temporary` memory's `observed_at`, the memory drops out of search; a `stable` memory from the same segment is still returned
- [x] `memhub list --stale` lists only stale memories, and `memhub search --include-stale` returns them
- [x] A duplicate of a stale memory merges into it and makes it current again
