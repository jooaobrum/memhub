# 08 — Implicit signals and the prefilter

**What to build:** Ingestion records the implicit feedback in each conversation and skips trivial segments without calling the LLM.

- **Feedback signals:** the JSONL source's optional `signals` file (the pilot's `feedback.jsonl`) is joined on the configured keys and mapped through config (`rating: down → feedback_down`, `up → feedback_up`).
- **Correction detector:** regex-first ("não, …", "na verdade", "errado", "not X but Y"). An optional cheap-LLM fallback can be switched on in config; it is off by default.
- Signals (`correction | feedback_down | feedback_up | error | rephrase`, with `message_id` and `detail`) are stored in the run row's `signals`.
- **Prefilter (no LLM):** skip the segment if it has fewer than `min_user_turns` user turns, or if every turn matches `skip_when` (e.g. `intent in [greeting, out_of_scope]`). Signals are still recorded for skipped segments.
- A correction signal forces the segment through the prefilter.

**Blocked by:** 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**Status:** done

- [ ] AC 10: a thumbs-down in the feedback file, or a correction detected in a segment, is recorded in `signals` for that run
- [ ] Prefiltered segments make zero LLM calls but still store their signals and advance the watermark
- [ ] A segment that `skip_when` would skip is still extracted when it contains a correction
- [ ] The correction regexes are unit-tested on Portuguese and English examples, including negative cases
- [ ] The LLM fallback is not called when it is disabled in config
