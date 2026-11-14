# Review edit verification

**Verdict**: FAIL
**Mutations**: 3 - probed C11, C8, C24
**Proofs**: `proofs.md` at 6e392a4
**Verifier**: independent sub-agent (author != verifier)

## Mutations injected

| Mutation | Location | Killed |
| --- | --- | --- |
| C11 any user may review a user-scope candidate (`row["user_id"] == actor.id` dropped from `_authorize_review`) | `src/memhub/service.py:277` | yes (at c6307c5) |
| C8 key compared raw (`_slot_text(...) != _slot_text(...)` -> `payload.get("key") != row["payload"].get("key")`), so `"1"` -> `1` counts as a key change | `src/memhub/service.py:297` | no at c6307c5; yes at 6e392a4 (builder's run) |
| C24 rejected-repeat threshold `>=` -> `>` in `_refuse_rejected_repeat` | `src/memhub/service.py:718` | yes (at c6307c5) |

## Own examples

- AC 21, workspace scope (no check names it): anna's workspace candidate "x" rejected, bob proposes a 0.95-similar workspace text -> refused on memory, SQLite and Postgres. Holds.
- AC 21, a correction (`memory_id=`) that rewrites a pending draft into the rejected text -> written on all three backends. Breaks (finding 2).
- AC 9-10, the owner of a user memory promotes it to workspace scope, then approves (with `fields`) or rejects the workspace candidate with only role `user` -> `PermissionDenied` on all three backends. Holds.

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| The AC 6 example `"1"` -> `1` was proved on a candidate whose stored key was already the int `1`, so validation made both sides equal and the `_slot_text` comparison was never exercised | mutation C8 (raw key compare) not killed at c6307c5 by `test_key_change_on_review` | store the candidate's key as the text `"1"` and edit it to a value that validates to `1` (builder reports this done in 6e392a4, mutation killed there) | `6e392a4` - C8 (`test_key_change_on_review`), mutation C8 killed |
| A correction bypasses the rejected-repeat rule: `propose(..., memory_id=X)` rewrites a pending draft into text a reviewer already rejected, without `user_requested`, and it is written. AC 21 says `propose` SHALL refuse it | throwaway test: reject "x" for anna, propose "draft", then `propose("fact", {"content": "x"}, memory_id=draft.id)` -> no `ValidationError` on memory, SQLite, Postgres. `MemoryService.propose` returns from the `memory_id` branch before `_refuse_rejected_repeat` | run `_refuse_rejected_repeat` (unless `user_requested`) before `amend_row` too, and add the case to C24 | `596c23e` - C34 (`test_rejected_repeat`) |
| C24 proves AC 21 on user scope only; the workspace half ("same workspace for workspace scope") has no proof, and a mutation of the scope/owner filter there would go unseen | `tests/test_review_propose.py:145` - every proposal in `test_rejected_repeat` is `scope="user"` | add a workspace-scope rejected candidate refused for another user of the same workspace and written for another workspace | `596c23e` - C34 (`test_rejected_repeat_workspace_scope`) |
