# deepagents backend verification

**Verdict**: FAIL
**Mutations**: 3 - probed C28, C18 (C31), C24
**Proofs**: `proofs.md` at 0333915
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| Moving a later line's id onto an earlier line archives the memory whose id was dropped, and edits (or flags against) the memory whose id it took. AC 26 says that line must change nothing | own example at 0333915: a page whose first line `Flies from CDG` is given the id of the second line `Likes aisles` returned `refused: memory check failed, try again` (an edit of the aisles memory was attempted), then `refused: line #<aisle> id changed`, then `archived #<cdg>`. Afterwards the CDG memory was `archived`. `diff()` in `src/memhub/pages.py` pairs by first occurrence: the first line that holds an id becomes an `edit`, the real owner of the id is `refused`, and `refused_texts` holds the wrong text, so the CDG line is then counted as removed | when one id appears on two written lines, refuse every line that holds it. Or pair each id only with a line whose text matches the current page, and refuse the rest. Only count a line as removed when its text is gone from the written page || `d28885b` - `diff()` refuses every line holding a repeated id and removes a line only when its id and text are gone; C26 now also proven by `test_id_moved_onto_another_line` (both orders) |
| C26's second case does not check the value it claims and covers only one order | `tests/test_pages.py:471-472` checks only that `"refused: line #" in lines(...)[0]`. It does not check which id is refused, that both memories stay `active`, or the case where the moved id lands on the earlier line (above) | assert the exact result lines and that both memories are still `active`, in both orders || `d28885b` - `test_id_moved_onto_another_line` asserts the exact result lines and both memories' history, in both orders (C26) |
| A whole-file write with no stamp is not tested: a mutation that accepts it survives | mutation `if m is None or m.group(1) != page.stamp` -> `if m is not None and m.group(1) != page.stamp` (`src/memhub/pages.py:489`) was not killed by C28's proofs. At HEAD the write is refused correctly (own run: `ValidationError: page changed since you read it`), but no test holds this | in `test_stale_stamp`, also write the page with its stamp comment removed, and assert `ValidationError(STALE)` and that no row was written || `d28885b` - `test_stale_stamp` writes the page with its stamp removed and asserts `ValidationError(STALE)` and no new row (C28) |
| C42 does not prove the stamp ignores the `rendered` time | `tests/test_pages.py:651-652` renders twice in the same second, so the `rendered` value is identical in both. A stamp that hashed the header would still pass | build two pages with different `now` (`build_page(..., now=...)`) and assert their stamps are equal while their `rendered` lines differ || `d28885b` - `test_stamp_tracks_content` builds pages a day apart and asserts equal stamps with different text (C42) |
| C6 does not cover the `reviewed_by = ""` case that AC 6 names ("not `auto`, not empty") | `test_tag_table` covers `alice`, `auto` and NULL only | add a row with `reviewed_by=""` and a quote, and assert it renders as `[said]` || `d28885b` - `test_tag_table` adds `reviewed_by=""` with a quote, rendered `[said]` (C6) |

## Notes

- Mutations: `assistant_only -> refused` (C18) was killed and `approve archive -> no archive` (C24) was killed. `missing stamp accepted` (C28) was not killed (finding above).
- Own example 2 (AC 1): with templates two levels deep that share a prefix (`org/{workspace_id}/people/{user_id}`, `org/{workspace_id}/shared`), `ls` showed one segment per level as it should, and `/org/w2/` returned `not found`. It held.
- The mutation run's proofs for C23, C24, C32 (Postgres), C37 and C45 went red during the run, after an earlier run of it had been killed. Run again alone, the Postgres contract proof and the C45 e2e suite passed (1/1 and 26/26). This looks environmental. `proofs.md` was restored to the builder's version at 0333915.
