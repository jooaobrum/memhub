# 34 — Upsert and conflict handling, tested heavily over time

**What to build:** memhub keeps its memories correct when the same person keeps talking over weeks. New
statements update, refine or contradict what is already stored, and the system must decide each case the same
way every time, keep the history, and never leave the ledger inconsistent.

The example that drives this ticket: on 2026-08-10 Joao says he is 18 and lives in Grenoble. A month later he says
"I'm 19 now". The `age` slot must be updated to 19 (version 2, with version 1 kept as history), the `city` slot
must be left alone, and nothing may be duplicated.

This ticket writes the tests first, then fixes whatever they show is missing in tickets 28–32. Keep fixes minimal.

## The policy (a candidate C against the active memory M of the same slot, or the closest one in the same area)

1. **Order by when it was said, not when it was ingested.** If C's `observed_at` is older than M's: the same value
   only adds evidence (M's `observed_at` does not move back); a different value is dropped as `outdated`.
2. **Same value:** merge the evidence, renew `observed_at` and `valid_until`, and count `seen_count` only for a
   new thread. No new version.
3. **Different value, C newer, M not verified.** For a keyed type, the judge decides between `updates` (replace:
   Grenoble → Paris, 18 → 19), `extends` (complementary: "lives with a French husband" + "has a daughter" become
   one statement) and `conflicts`. It is not called when the normalised texts are equal.
   - `updates` → supersede: new version N+1, version N kept as history.
   - `extends` → new version whose content merges the two statements. The judge may only use the two
     statements' own words, and the evidence of both accumulates.
   - `conflicts` → a `candidate` with `conflicts_with`; M stays active.
4. **Immutable keys.** A key can set `mutable: false` (Habitantes: `nationality`). A differing value for it is
   never an update: it becomes a conflict candidate, unless the judge says `extends` ("I'm also Portuguese").
5. **Verified memories are protected.** If M is verified (a person confirmed or edited it), the extractor never
   supersedes it: a differing C becomes a conflict candidate, and the same value adds evidence.
6. **Stated beats inferred.** An inferred C never supersedes a stated M. A stated C may supersede an inferred M.
7. **Stale M** (`valid_until` passed): a differing C supersedes it, and the same value renews it.
8. **One slot, one value per segment.** If two candidates in one segment target the same slot, only the one from
   the later message goes on; the other is dropped as `same_slot_in_segment`.
9. **Idempotent.** Processing the same segment again (same message ids) creates no version, does not change
   `seen_count` and does not duplicate evidence (evidence is deduplicated by message id).
10. **History.** Every superseded version stays readable. `memhub history <memory_id>` lists versions with value,
    `observed_at`, evidence and who created them.
11. **Terms.** A definition of an existing term adds aliases (up to the cap). An alias that already belongs to a
    different active term becomes a conflict candidate for the admin.
12. **Non-keyed Facts** in the same area follow the existing judge (`same | updates | conflicts | unrelated`) with
    rules 1, 5 and 6 applied.

Config additions: `mutable` on a key (default `true`), and the `extends` verdict for the judge.

## Tests (scenario-based, real Postgres, fake extractor and judge, controlled message timestamps and `now`)

| # | Scenario | Expected |
|---|---|---|
| S1 | Aug 10: "I'm 18, I live in Grenoble". Sep 10, new thread: "I'm 19 now" | `age` v2 = 19 active, v1 (18) superseded with its quote; `city` unchanged, one version |
| S2 | The Sep 10 statement is ingested first, then the Aug 10 one | `age` stays 19; the Aug 10 candidate is dropped `outdated` |
| S3 | A month later: "I live in Grenoble" again | evidence +1, `observed_at` and `valid_until` moved, `seen_count` 2, no new version |
| S4 | Grenoble, then "I moved to Paris" | judge `updates`: `city` v2 = Paris, Grenoble kept in history |
| S5 | Nationality Brazilian, later "sou portuguesa" | `mutable: false`: conflict candidate, `nationality` unchanged; `approve --resolve keep_both` / `replace` resolve it |
| S6 | Nationality Brazilian, later "também sou portuguesa" | judge `extends`: one row "Is Brazilian and Portuguese" with both quotes |
| S7 | `family`: "arrived with my French husband", later "my daughter is 5" | judge `extends`: one merged statement, not two rows and not a replacement |
| S8 | A person edits `city` to Lyon (verified), later the extractor sees "I live in Paris" | conflict candidate; Lyon stays active; `approve --resolve replace` makes Paris active and keeps Lyon in history |
| S9 | `residence_status` past its `valid_until`, then a new value / the same value | new value supersedes; the same value renews |
| S10 | (with `allow_inferred: true`) stated "lives in Grenoble", later inferred "lives in Paris" | stated row untouched; the inferred candidate is dropped (`inferred`), never applied |
| S11 | One segment: "moro em Lyon", then "na verdade moro em Paris" | only Paris continues; Lyon dropped `same_slot_in_segment` |
| S12 | Re-ingest the same segments with `--reprocess` | zero new versions, `seen_count` and evidence unchanged |
| S13 | A Fact "Has a Brazilian driving licence" (documents), later "exchanged it for a French one" | judge `updates`: v2 supersedes; a fact in another area is never compared |
| S14 | A term defined again with a new alias; an alias already used by another term | alias added up to the cap; the clashing alias becomes a conflict candidate |
| S15 | Two areas merged (ticket 30) while memories link to them | no memory points to the archived area; invariants hold |

**Invariants**, checked after every scenario and after every step of the randomised test:
- at most one active version per `memory_id`, and at most one active row per (owner, keyed type, key);
- version numbers are contiguous from 1;
- every active row has at least one evidence quote, and its `observed_at` equals its newest evidence date;
- every superseded version is still readable through `history`;
- no `in_area` link points to an archived or missing area;
- no evidence entry is duplicated (same message id twice).

**Randomised test.** A seeded generator (fixed seed printed on failure) applies 200 sequences of 5–15 steps to one
user: random slot values, message dates in and out of order, verified edits, repeated statements, and re-ingests.
After each step the invariants must hold, and the final active value of each slot must equal the value from the
newest unverified-or-verified-by-rule statement (an oracle written in the test, independent of the pipeline).

**Blocked by:** 28 — Profile and Preference slots; 29 — Areas from seeds; 32 — Terms from explicit definitions

**Status:** done

- [ ] AC 30–36 (see the spec) are each covered by at least one scenario above; S1–S15 pass against real Postgres
- [ ] The randomised test (200 seeded sequences) passes, and a deliberately broken supersede (a test-only patch) makes it fail
- [ ] `memhub history <memory_id>` lists every version with value, `observed_at`, evidence and creator
- [ ] The judge is called at most once per candidate, and never when the normalised texts are equal (checked with a counting fake)
- [ ] The run summary counts `outdated`, `same_slot_in_segment`, conflicts opened and extends applied
- [ ] Any fix to tickets 28–32 needed to pass the scenarios is listed in the ticket's final note, with the scenario that exposed it

## Final note: fixes to tickets 28-32 exposed by the scenarios

- Keyed slots used to supersede without a judge (28): S1/S4/S6/S7 need the judge (`updates`/`extends`), so `reconcile` now asks it once per differing candidate; two existing tests were updated accordingly.
- No `outdated`/verified/inferred rules existed (S2, S8, S10): added to `pipeline/reconcile.py`.
- `merge_evidence*` did not deduplicate by message id (S12): fixed in `store.py`.
- A person's edit did not set `verified` for a non-admin owner (S8): `MemoryService.edit` now always verifies.
- `approve --resolve keep_both` on a keyed slot would violate the one-active-row index (S5): it now writes one merged new version.
- Two candidates for one slot in a segment (S11): dropped as `same_slot_in_segment` in `ingest.py`.
