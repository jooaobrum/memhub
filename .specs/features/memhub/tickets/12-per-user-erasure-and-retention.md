# 12 — Per-user erasure and dropped-candidate retention

**What to build:** memhub meets the right to erasure and does not keep dropped personal data longer than the configured retention.

- `memhub delete --user <user_id>` permanently deletes every user-scope row of that user (all versions and statuses). It also deletes that user's rows from `<prefix>_memory_runs` (watermarks, signals and dropped candidates).
- Dropped candidates in `<prefix>_memory_runs.dropped` are purged after `retention.dropped_days`, for example as part of each ingest run.

**Blocked by:** 04 — Versioned edit, archive and delete by id; 06 — Tracer bullet: JSONL ingest → extract → route → ledger

**Status:** done

- [ ] AC 11: `memhub delete --user <id>` removes all user-scope rows of that user and that user's rows in `<prefix>_memory_runs`, and leaves other users' and workspace rows intact
- [ ] Dropped entries older than `retention.dropped_days` are purged, and newer ones are kept
- [ ] After erasure, a re-ingest of the same logs is treated as new (documented behaviour)
