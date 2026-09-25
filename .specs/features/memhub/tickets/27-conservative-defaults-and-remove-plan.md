# 27 — Conservative defaults, and remove `Plan`

**What to build:** By default memhub stores only what the user or a tool actually said, and only the kinds a
project has turned on. The `Plan` kind is removed completely. This is the guardrail against inventing memories
and against generating thousands of them.

- New config `guardrails.allow_inferred` (default `false`). When false, a candidate with `assertion = inferred`
  is dropped with the reason `inferred` after grounding and before scoring. The `assertion` column and the
  score's inferred value stay, so a project can turn inference on.
- Confirm that a type with `extract: false` is never offered to the extractor and never produced by it; add a
  test if none exists.
- **Remove `Plan` completely:** the type and its registration, the extractor prompt rule that routes intentions to
  `plan`, the `plan` entries in `memhub.yaml`, `memhub.example.yaml` and the test config fixtures, and the tests
  that only exist for Plan. The prompt says instead that a stated intention is not stored, and that the durable
  fact behind it goes to a Fact or Profile. Old `plan` rows would fail validation on read, so the pilot ledger is
  rebuilt from zero (ticket 35) and no migration is written.
- **Keep** the judge's `updates` verdict and the supersede path from ticket 21: they are generic (a new city, a
  new residence status). Mark ticket 21 as partly reverted.
- `Episode` stays enabled in Habitantes as a secondary kind, with a small cap (`max_active: 10`) and its low type
  prior.
- `memhub.example.yaml` documents `guardrails` and shows which kinds are on or off.
- The run summary shows `inferred` as a drop reason.

**Blocked by:** 26 — Remove the automatic context Episode

**Status:** done

- [x] AC 20: with `allow_inferred` false (the default) an inferred candidate is dropped as `inferred`; with it true the candidate follows the normal path
- [x] A type with `extract: false` is not in the extractor's schema, and an extraction naming it is rejected like any invalid output
- [x] `Plan` no longer exists: no type, no config entry, no prompt rule; the registry and extractor schema do not list it, and the full suite passes without the Plan-only tests
- [x] The `updates` verdict still supersedes a fact (a new city) as a new version
- [x] The Habitantes config loads with `episode` on (`max_active: 10`) and no `plan`
- [x] The dropped reason `inferred` appears in the run summary
