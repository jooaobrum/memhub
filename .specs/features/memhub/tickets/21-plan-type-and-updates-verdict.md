# 21 — Plan type and the judge's `updates` verdict

**What to build:** Stated intentions ("I'll apply to Grenoble next year", "I'm moving to Paris") are stored as
plans with a time window and a status. When a later message tells the outcome, the plan gets a new version and
the old one stays in the history.

- Add `Plan` as a built-in type. Its payload adds `status: open | done | abandoned` (default `open`), and its
  window end is the row's `valid_until` from ticket 19. Keep it this small.
- Register it in the default config and in the pilot `memhub.yaml` (`retrieval: search`, `type_prior: 0.7`).
- Prompt rule: a stated intention or plan is a `plan`, not a `fact`.
- The judge's verdicts become `same | updates | conflicts | unrelated`. The judge sees both statements with
  their `observed_at` dates. `updates` means the new statement is a later state of the same thing, e.g. a plan
  now done or a new city.
- `updates` → the candidate becomes version N+1 of the existing memory, and version N becomes `superseded`,
  in one transaction. User scope → `active`. Workspace scope → a `candidate` for review, as with any other
  edit made by the extractor.

**Blocked by:** 19 — Validity window

**Status:** done — the Plan type is removed in v1.2 by ticket 27; the `updates` verdict and supersede path stay

- [x] `Plan` validates through the registry, and a payload with an unknown `status` is rejected
- [x] A (fake) extraction of a plan dated "next year" is stored as a `plan` with `status: open` and `valid_until` at the end of that year
- [x] AC 18: when the (fake) judge returns `updates`, the candidate is inserted as version N+1 and version N becomes `superseded`; no conflict candidate is created
- [x] `same`, `conflicts` and `unrelated` behave as before
- [x] A workspace-scope `updates` becomes a `candidate`, and the existing active version is untouched until it is approved
