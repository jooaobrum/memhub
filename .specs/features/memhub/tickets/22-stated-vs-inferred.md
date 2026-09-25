# 22 — Stated versus inferred claims

**What to build:** A memory the user actually said can be told apart from one the extractor deduced. Pilot
run 3 stored "Is Brazilian and lives in Grenoble" from a question about the best restaurants "according to
Brazilians in Grenoble", and it looked exactly like a stated fact.

- New column `assertion`: `stated | inferred`, default `stated`. It is added through the upgrade path from
  ticket 18.
- The extraction output format gains `assertion`. The prompt rule: `inferred` whenever the content goes beyond
  what the user literally said.
- In the admission score, the `evidence` feature is 1.0 for stated claims with user or tool evidence, 0.6 for
  inferred ones, and stays 0.4 for assistant-only Episodes.
- Memories added by hand are `stated`.
- Search results include `assertion`, and `list` / `search` output show it.

**Blocked by:** 18 — Memories dated by when the user spoke

**Status:** done

- [x] AC 17: an inferred candidate gets 0.6 for the evidence feature, and search results include `assertion`
- [x] With the same utility, novelty and type, an inferred candidate scores lower than a stated one
- [x] A missing `assertion` in the extraction output fails validation like any other invalid output
- [x] `list` and `search` show which memories are inferred
