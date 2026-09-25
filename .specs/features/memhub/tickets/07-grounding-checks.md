# 07 — Grounding checks with recorded drops

**What to build:** No ingested memory can contain a hallucinated claim. After extraction, rule-based checks drop any candidate that fails them, and each drop is recorded with its reason in the run's `dropped` list:

- **`ungrounded`:** each evidence quote must appear in the content of its `message_id`, compared after normalising whitespace and case.
- **`assistant_only`:** a `fact` whose evidence all has `claim_source = assistant`. The agent's own output may only appear inside an `Episode`.
- **`injection`:** candidate text that matches the prompt-injection patterns (the same scanner used by `memhub add`).

The run summary shows dropped counts by reason.

**Blocked by:** 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**Status:** done

- [ ] AC 3: a candidate whose quote is not a substring of the segment's messages is dropped with reason `ungrounded` in `dropped`
- [ ] AC 4: a `Fact` whose only evidence has `claim_source = assistant` is dropped with reason `assistant_only`
- [ ] An `Episode` with assistant-only evidence is kept
- [ ] Quote matching ignores differences in whitespace and case
- [ ] Injection-pattern candidates are dropped with reason `injection`
- [ ] The run summary includes the drop breakdown by reason
