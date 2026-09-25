# 36 — Habitantes pilot re-run on v1.2, from zero

**What to build:** A re-run of the pilot that shows whether the smaller model holds up on the real logs: Profile
and Preference slots, Facts in areas, no context Episodes, no inferred claims. The target is a small ledger, not
a big one.

- Update the Habitantes `memhub.yaml`: `profile`, `preference` and `fact` on as the core (keys, and `area: required`
  for Fact), `episode` (`max_active: 10`) and `term` (workspace scope) on as secondary kinds, `skill` off, no
  `plan`, the 19 seed areas with one-line descriptions, `areas` caps, `scopes: [user, workspace]`,
  `guardrails.allow_inferred: false`. Adjust the extraction instructions to the slot, area and no-inference rules.
- Check the area assignment with Jev's Choice primitive (as in `pilot/jev/`): how often Jev's pick matches the
  extractor's, and which areas overlap. Calibrate the area descriptions if they confuse the model, at most three
  iterations.
- Empty EVERY `habitantes_` table (drop them all and run `memhub init` fresh), verify all are empty, then run
  `memhub ingest --source jsonl --reprocess` with the real models. A second ingest must make no LLM calls.
- Also run it in **two phases** to imitate a month later: empty the tables, ingest only the logs before a cutoff
  date (a filtered copy of the JSONL), then ingest the rest. Compare the final ledger with the single-run
  ledger: the same slots per user, no duplicates, and every difference explained by the policy in ticket 34
  (superseded, outdated, conflict). Report the counts of each.
- Write `pilot/run6_report.md` next to runs 3–5: counts by kind, memories per user, areas created and proposed,
  slots filled, drops by reason (including `inferred`, `no_area`, `unknown_key`, `area_cap`), tokens, and the
  Jev and agent-read judgement of quality, stated as such (no human labels).
- Regenerate the memories HTML ([pilot/run5_memories.html](../pilot/run5_memories.html) is the v1.1 version) so
  each user shows a Profile card, a Preferences card and one page per area.

**Blocked by:** 27 — Conservative defaults and remove Plan; 28 — Profile and Preference slots; 30 — Model-proposed areas; 31 — Area pages; 32 — Terms from explicit definitions; 34 — Upsert and conflict handling

**Status:** ready-for-agent

- [ ] Every table was empty before the run, and the second ingest made 0 LLM calls and created nothing
- [ ] No active memory without a quote, no Fact without an area, and no slot with two active rows
- [ ] Average active memories per user is small (target: 2–3 or fewer), no context Episodes exist, and the core kinds (Profile, Preference, Fact) outnumber Episodes and Terms
- [ ] The two-phase ingest ends with no duplicate slot rows and no unexplained difference from the single run
- [ ] The report compares runs 3–6 and states which quality numbers are Jev-judged or agent-judged
- [ ] The per-user HTML shows profile, preferences and area pages
