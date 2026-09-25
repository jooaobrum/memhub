# 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**What to build:** `memhub ingest --source jsonl` reads interaction logs, extracts candidate memories with one structured LLM call per segment, and writes them to the ledger. The path is thin but complete: it has no grounding, scoring or reconcile yet (tickets 07–10 add those).

- **Source adapter:** a `SourceAdapter` protocol yields normalised `Interaction`s (`thread_id, user_id, workspace_id, message_id, role, content, timestamp, trace_id, metadata`). The JSONL adapter maps fields through config. With `one_line_per: turn`, each line emits a user message and an assistant message. Malformed lines, and lines missing `thread_id` or `message_id`, are skipped and counted in the run summary.
- **Segmenting:** interactions are grouped by `thread_id` and sorted by timestamp. A segment is the set of messages after the thread's watermark, and it is processed only when its newest message is older than `now - segment_idle`.
- **Extraction:** one call to `llm.extractor` with structured output. The schema is a discriminated union of the enabled types (`extract: false` types are excluded) and is generated from the registry. The call returns at most `max_candidates` candidates, each with fields, evidence, `claim_source`, utility and `applies_generally`. The prompt says most segments should yield nothing and that user statements are preferred. Invalid structured output means zero candidates and a run `status = extract_error`.
- **Route:** user scope becomes `active` with `verified=false`. Workspace scope becomes `candidate`. `created_by = "extractor"`. Evidence stores only the quotes, never full transcripts.
- **Transactions:** each segment's memory rows and its `<prefix>_memory_runs` row (with the watermark) commit in one transaction. If an LLM or embedding call fails, nothing is written for that segment and it is retried on the next run.
- **Run summary:** prints `run_id`, threads, segments, candidates proposed, memories created and skipped lines.

**Blocked by:** 02 — Add memories by hand and list them

**Status:** done

- [ ] AC 1: memories are created only for segments idle ≥ `segment_idle`, and a watermark per thread is saved in `<prefix>_memory_runs`
- [ ] AC 2: a second run with no new lines creates no memories and makes zero LLM calls (asserted with a fake LLM call counter)
- [ ] AC 7 (write half): an extracted user-scope memory is stored `active`, `verified=false`
- [ ] Malformed lines and lines missing ids are skipped and counted
- [ ] A simulated LLM or embedding failure leaves no partial rows and no watermark for that segment
- [ ] Invalid extractor output records `extract_error` and zero candidates
- [ ] Tests use a fake LLM that returns fixed candidates, and a fake embedder
- [ ] Every memory version references the `trace_id`s it came from
