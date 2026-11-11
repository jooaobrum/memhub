# Push ingestion verification

**Verdict**: FAIL
**Mutations**: 3 - probed C8 (`after_watermark` slice off by one: killed), C9 (repeat counter always 0: killed), C1/C2 (`_push_event`: a superseded candidate reported `UPDATE` instead of `PENDING`: not killed, by the full suite either) - 2 of 3 killed
**Proofs**: `proofs.md` at 3c91eb2 (19 pass, 0 fail); mutations run by the Verifier on a copy of 3c91eb2
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| A sliding window of messages with no `timestamp` and a repeated reply drops the new messages without a word (AC 8 window example, AC 9 ids) | own example: push `[u:"hi", a:"Noted", u:"I live in Lyon", a:"Noted"]`, then the window `[u:"I live in Lyon", a:"Noted", u:"I work as a welder", a:"Noted"]` -> `results == []`, nothing extracted. The repeat counter `n` counts within the call, so the window's 2nd `"Noted"` (n=1) gets the id of the first call's 2nd `"Noted"` - the watermark - and `after_watermark` returns `[]`. With distinct timestamps the same window works (checked) | make the counter independent of where the window starts, or make `after_watermark` treat a watermark that is not the last occurrence of an id-equal suffix as not found (e.g. compare the run row's `first_message_id`..`last_message_id` span, or match the watermark only when the messages before it in this call also match the previous run's); at the least, state the limitation in door 2 and add the window-with-repeats case to C8 | |
| A new version of a reviewed workspace memory that waits for review is reported `UPDATE`, not `PENDING` (door 1: "`UPDATE` (`PENDING` when the new version waits for review)"), and no test fails | mutation in `memory.py` `_push_event`: check `outcome == "superseded"` before `row["status"] == "candidate"` - `test_events_follow_the_reconcile_decision` passes, full suite passes (856, the 4 failures are copy artefacts: missing `memhub.yaml` / git history). `route.supersede` does write `as_candidate=True` for `scope != "user"` with `review` | in C2 push a newer value for a keyed slot of a reviewed workspace type and assert `("PENDING", "candidate", version 2)` | |
| C9 asserts "another thread -> another id" against the other user, not against the base message | `tests/test_push.py:238` - `msg_ids(one) != msg_ids(one, user_id="bob") != msg_ids(one, thread_id="other")` is `a != b and b != c`; `a != c` (same user, other thread) is never asserted | split into `msg_ids(one) != msg_ids(one, user_id="bob")` and `msg_ids(one) != msg_ids(one, thread_id="other")` | |
