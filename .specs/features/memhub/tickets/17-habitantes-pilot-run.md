# 17 — Habitantes pilot: config, real run, precision sample

**What to build:** A pilot run of memhub on the real Habitantes logs that shows whether extraction is good enough. This is the only pilot deliverable. The Habitantes agent itself is not changed.

- Write the Habitantes `memhub.yaml`: prefix `habitantes`, a JSONL source over the interactions and feedback logs with the documented field mapping, built-in types only, `user` scope only, no entity types, and OpenRouter chat models plus OpenAI `text-embedding-3-small`.
- Add a pgvector Postgres to the Habitantes local docker-compose.
- Run `memhub init` and `memhub ingest --source jsonl` on the real logs, and capture the run summary (threads, segments, proposed, dropped by reason, merged, created, tokens, cost).
- Label a sample of ≥ 50 extracted memories for correctness/usefulness and grounding. Report extraction precision (target ≥ 0.8), hallucination rate (target 0), and a drop analysis by reason with suggested changes to the admission weights.

**Blocked by:** 09 — Admission score; 10 — Reconcile; 11 — Thread-close final pass and ingest flags; 12 — Per-user erasure and dropped-candidate retention; 13 — Run summaries with tokens and cost

**Status:** done: pilot runs 1–3 saved; the labelled precision sample of ≥ 50 memories was never produced

- [ ] The pilot `memhub.yaml` loads and `memhub init` succeeds against the local pgvector
- [ ] A real ingest run completes and its summary is saved
- [ ] A second run without new logs makes no LLM calls
- [ ] A labelled sample of ≥ 50 memories is saved, with precision and hallucination rate computed
- [ ] The drop analysis by reason is written up, with any proposed weight or threshold changes
