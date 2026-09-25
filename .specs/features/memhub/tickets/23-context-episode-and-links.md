# 23 — Context Episode and `derived_from` links

**What to build:** Each claim knows the situation it was said in. A segment that yields claims also yields one
Episode describing the situation, and every claim links to it. This replaces the pilot's Fact/Episode pairs
with identical content, and search returns the context along with the claim.

- New column `links`: a list of `{kind, memory_id}`, with a GIN index. It is added through the upgrade path
  from ticket 18. v1.1 writes only `kind: derived_from`. `conflicts_with` stays its own column.
- The extraction output format gains a top-level `context`: an Episode, required whenever there is at least one
  claim (a `context` with no claims is accepted and stores nothing, recorded as `no_claims`). It does not count toward `max_candidates`. Its `content` is a one-line
  situation, and the prompt says it must not repeat the claims.
- The context Episode is grounded like any candidate but not scored:
  - it is admitted if at least one claim from its segment is admitted;
  - if every claim is dropped, it is dropped with the reason `no_claims`.

  It goes through reconcile as an Episode, so it can merge into an existing one.
- The context Episode is routed first. Every other memory written from the segment gets a `derived_from` link
  to the Episode's `memory_id`, or to the memory the Episode merged into. When a claim merges into an existing
  memory, the link is added if it isn't already there.
- In search, each hit that has a `derived_from` link comes back with the linked Episode's current active
  version as `context`. This is one hop, with no recursion.
- The Episode's expiry needs no special code: it comes from the type-level `ttl` added in ticket 19.

**Blocked by:** 18 — Memories dated by when the user spoke

**Status:** done — reverted in v1.2 by ticket 26 (the automatic context Episode and `derived_from` links are removed; the `links` column stays)

- [x] AC 16: a segment producing a context Episode and two claims stores three memories, and both claims have `derived_from` pointing at the Episode's `memory_id`
- [x] When the Episode merges into an existing Episode, the claims link to that existing `memory_id`
- [x] When every claim is dropped, no Episode is stored and the run records `no_claims`
- [x] The context Episode does not count toward `max_candidates`, and output with claims but no `context` fails validation
- [x] Search returns the linked Episode's content as `context` for a claim, and nothing for a memory without links
- [x] A claim merged twice from the same segment's Episode does not get a duplicate link
