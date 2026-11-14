# Search filters verification

**Verdict**: FAIL
**Mutations**: 3 - probed C8, C9, C25
**Proofs**: `proofs.md` at af1c687
**Verifier**: independent sub-agent (author != verifier)

## Findings

| Finding | Evidence | Fix | Closed |
| --- | --- | --- | --- |
| A null `valid_until` that passes a datetime `lte` breaks no test (AD-050, AC 10) | mutation `null valid_until passes a datetime lte` (`rules.py:251`, drop `and self.lte is None`) not killed: C9 builds only `gte` and `lt` trees | add a `TimeRange("valid_until", lte=<datetime>)` case to `test_filter_valid_until_bounds` that drops the null row | `49a9242` - C38 (`test_filter_parsed_time_bounds`: datetime `lte` drops the null row, on every backend) |
| The date-only `lte` rule (whole UTC day, AD-049) in `filters.py` is not tested at all. C8 and C9 build `TimeRange(lt=...)` by hand, so their claims about `{"lte": "2026-10-31"}` / `{"lte": "2026-12-01"}` are proven at the tree level, not at the parser level the criterion names | extra probe: `filters.py` `lt = upper + timedelta(days=1)` -> `lt = upper`. The full suite stayed green apart from the 2 failures it also has without the mutation (`memhub.yaml` is missing from the worktree) | assert through `Memory.search` / `get_all` (or `parse(...).tree()` against a backend) that `{"observed_at": {"gte": "2026-10-01", "lte": "2026-10-31"}}` keeps 10-31T15:00Z and drops 11-01T00:00Z, and that `{"valid_until": {"lte": "2026-12-01"}}` keeps 12-01T10:00Z | `49a9242` - C38 (date-only `lte` through `filters.parse`: 23:59:59.999999 kept, next day 00:00 dropped) |
| Under `OR`, naming one guarded subject lifts the `require_filter` guard for the whole type, so another patient's memories come back: `{"OR": [{"entities": [patient:p1]}, {"types": ["fact"]}]}` returns `patient p2 allergy` through `search` and `get_all`. Before #25, `entities: [p1]` never surfaced p2. AC 26 / AD-033 intent: a guarded subject comes back only when the filter asks for it | throwaway test on the in-memory backend: result `{patient p1 allergy, patient p2 allergy, a plain fact}`. C25 (`test_search_filters.py:224`) asserts only `"patient p1 allergy" in ...`, which is looser than the criterion and lets the leak pass | decide the rule (a positive `EntityIs` should lift the guard only for the ids it matches, e.g. AND the guard with `NOT type OR <that predicate>`), then assert equality on the OR result so p2 is excluded | `49a9242` - AD-052, C37 (`test_guard_scoped_to_the_named_subject`) and C25 now asserts the exact set; `get_all` keeps no guard, as before #25 (AD-033 is search only) |
| `MemoryMiddleware.MEMORY_FRAME` prompt wording changed in the feature commit (e4965bd). No plan criterion or check covers it, and it is outside #25's scope | `git diff 65233c5..af1c687 -- src/memhub/middleware.py`, lines 33-39 | move it to its own commit/PR, or record it in the plan with its evidence | `b249fce` - the rewording is reverted on this branch; it stays uncommitted in the other work it came from |
