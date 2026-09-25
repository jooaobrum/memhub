# 26 — Remove the automatic context Episode

**What to build:** Ingestion stops writing an Episode for every conversation that yields a claim. In run 5 that
made 14 of 31 memories ("Question about X"), all low value. An Episode is now stored only when the extractor
proposes it as an ordinary candidate with all its fields taken from the text. This mostly reverts ticket 23. The
`links` column and the `add_link` helper stay, because ticket 29 reuses them.

- The extraction output has no `context` field. Remove it from the schema, its validator (claims require a
  context), the prompt text and the FINAL PASS wording that mentions it.
- Remove the special ingest flow for the context Episode: routing it first, `derived_from` links, the
  `no_claims` drop for the context, and the cleanup when every claim is capped.
- Claims no longer get a `derived_from` link, and search no longer returns `context`.
- The middleware format drops the `— context: …` part (ticket 24); the rest of the format stays.
- The extractor prompt says: propose an `episode` only when the text gives a situation, an action and an
  outcome, and never invent a missing part.
- Mark ticket 23 as reverted in its Status line, with a pointer here.

**Blocked by:** None — can start immediately

**Status:** done

- [x] AC 19: a segment with claims stores only those claims; no automatic Episode is created and the extraction schema has no `context` field
- [x] Search results and middleware items have no `context`; nothing else in their format changes
- [x] An `episode` candidate proposed by the (fake) extractor with all its fields is stored and searchable like any other memory
- [x] The run summary no longer reports `no_claims` drops; tests that assumed a context Episode are updated without weakening what else they assert
- [x] `links` column, its index and `add_link` still exist and are covered by the store tests
