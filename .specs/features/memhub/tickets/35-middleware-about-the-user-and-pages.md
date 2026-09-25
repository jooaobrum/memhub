# 35 — Middleware: "About the user" block and the page of the query's area

**What to build:** An agent using memhub starts each thread knowing who the person is and how to answer, and gets
the relevant area page when the question is about a topic. Still not wired into Habitantes.

- Thread start: the user's active Profile and Preference rows form one "About the user" block, Profile first,
  each capped at its `max_chars`, stale rows skipped. It is cached for the whole thread.
- Each turn: search as today (now with the term expansion from ticket 33 and the area boost from ticket 29),
  plus the page of the area that best matches the query (title, summary marked auto-summary, top details), within
  `areas.page_max_chars`. A query that matches no area injects no page.
- The item format loses the `— context: …` part (see ticket 26) and keeps the as-of date and `inferred` label.
- Every injected `memory_id@version`, including the area row and the Profile and Preference rows, is recorded in
  `memhub_injected` and in the trace metadata, as in ticket 24.
- `search_memory` results use the same format.

**Blocked by:** 28 — Profile and Preference slots; 31 — Area pages; 33 — Terms expand search

**Status:** done

- [x] AC 29: against a fake agent, the thread-start block holds the user's Profile then Preferences, skips stale rows, and respects `max_chars`
- [x] A turn about housing injects the housing page (title, auto-summary, details) and the search hits; a turn that matches no area injects no page
- [x] `memhub_injected` lists the Profile, Preference, area and search rows injected, with versions
- [x] The existing middleware tests still pass with the `context` part removed
