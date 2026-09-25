# 13 — Run summaries with tokens and cost

**What to build:** An operator can check what past ingestion runs did and how much they cost, which the pilot needs for its drop analysis.

- Each segment's run row records `tokens_in`, `tokens_out` and `cost_usd`, taken from the LLM call metadata (extractor + judge).
- `memhub runs [--last N]` shows, per run: `run_id`, source, threads and segments processed, candidates proposed, dropped by reason, merged, created, `extract_error` count, tokens and cost.

**Blocked by:** 07 — Grounding checks with recorded drops

**Status:** done

- [ ] The token counts reported by the fake LLM are persisted per segment and aggregated per run
- [ ] `memhub runs --last N` lists the N most recent runs with all the summary fields
- [ ] Dropped-by-reason totals match the `dropped` entries in the run rows
