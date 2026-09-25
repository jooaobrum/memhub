# 11 — Thread-close final pass and ingest flags

**What to build:** Closed threads get one episode-level summary, and operators control re-runs.

- **Final pass:** when a thread has been idle for ≥ `thread_close` (default 7 d), it gets exactly one extraction over the whole conversation. This pass is restricted to `Episode` and episode-like project types. The run row sets `final_pass = true`, and the pass never runs twice for the same thread.
- **`--reprocess`:** ignores watermarks and reprocesses the selected threads.
- **`--dry-run`:** runs the full pipeline and prints the summary, but writes nothing (no memories, no run rows).
- **`--thread <id>`:** limits the run to one thread.

**Blocked by:** 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**Status:** done

- [ ] A thread idle ≥ `thread_close` gets exactly one final pass, and later runs skip it
- [ ] The final-pass extraction schema offers only episode types
- [ ] `--dry-run` leaves both tables unchanged
- [ ] `--reprocess` re-extracts already watermarked segments
- [ ] `--thread` processes only the given thread
