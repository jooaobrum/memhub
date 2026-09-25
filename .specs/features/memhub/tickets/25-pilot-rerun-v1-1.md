# 25 — Habitantes pilot re-run on v1.1

**What to build:** A re-run of the pilot on the real Habitantes logs that shows whether v1.1 fixes the problems
found in run 3 (research.md §10). It also produces the labelled precision sample that ticket 17 never did.

- Update the pilot `memhub.yaml` extraction instructions:
  - drop the example that rewrites "deciding whether to apply now or next year" into a timeless fact (it
    becomes a dated `plan` now);
  - align the rest with the v1.1 prompt rules: dates, durability, plans, assertion, context.
- Clear the pilot ledger, run `memhub init`, and re-ingest with `--reprocess`. Save the run summary next to the
  earlier runs.
- Check the v1.1 outcomes on the new ledger and write them up next to run 3's numbers.
- Label a sample of ≥ 50 memories for correctness/usefulness and grounding, and report precision (target
  ≥ 0.8) and hallucination rate (target 0).

**Blocked by:** 19 — Validity window; 21 — Plan type and `updates` verdict; 22 — Stated versus inferred; 23 — Context Episode and links

**Status:** in-progress: run 5 done (`pilot/run5_report.md`); quality judged by Jev and an agent read, not by a human sample; waiting for the user's acceptance

- [x] No memory has an `observed_at` equal to the ingestion date unless its message was actually sent that day
- [x] No user has a Fact and an Episode with the same content; claims link to their context Episode instead
- [~] DROPPED (user decision, run 4 review): short-lived needs such as a dentist, housing or job vacancies are not worth storing, so they no longer need a `valid_until`
- [x] Plans found in the logs (e.g. moving to Paris, applying for citizenship, "now or next year") are stored as `plan` with a window where the text gives one (run 5: the "now or next year" plan has 2026-2027 in its content and `valid_until` 2027-12-31; the CNH-exchange and second citizenship threads are question-only, so they hold no plan)
- [x] The run summary is saved and compared with run 3 (proposed, created, merged, dropped by reason, tokens)
- [x] Memory quality is judged by Jev (all 31 active memories, `pilot/run5_jev_judged.json`) and by an agent read of 25 of them (seed 5), reported as "Jev-judged" and "agent-judged" in `pilot/run5_report.md`. This replaces the human-labelled sample of ≥ 50 (the ledger has fewer than 50 memories); it is NOT a human-labelled precision
