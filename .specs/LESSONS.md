# LESSONS - auto-maintained by scripts/lessons.py

> Machine-owned. Do NOT hand-edit. Changes are overwritten on the next `lessons.py` write.
> Canonical state lives in `.specs/lessons.json`. Edit lessons only via the script.
> promote_threshold=2 distinct features · window_days=45 · quarantine_threshold=2

## Confirmed (load these at Plan/Checks)

Corroborated across multiple features. Safe to apply as guidance.

_none_

## Candidates (under observation - do NOT load as guidance yet)

Seen once or not yet corroborated. Tracked, not trusted.

### L-001 - Pin every comparison boundary a backend mirrors with a row exactly at the boundary.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C24 / memory.py _stale (storage)
- last seen: 2026-11-11T17:57:06Z

### L-002 - Test each column of a composite uniqueness key by varying that column alone.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C21 / memory.py _check_unique (storage)
- last seen: 2026-11-11T17:57:06Z

### L-003 - Compare JSON keys the way the database extracts them (->> text), not as Python values.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C29 / memory.py _check_unique (storage)
- last seen: 2026-11-11T17:57:06Z

### L-004 - Scope a shared in-process store by every setting that separates tables in the real database, table prefix included.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C30 / storage/__init__.py open_backend (storage)
- last seen: 2026-11-11T17:57:06Z

### L-005 - Normalize naive datetimes to UTC wherever a backend stands in for timestamptz.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C31 / memory.py add_memory (storage)
- last seen: 2026-11-11T17:57:06Z

### L-006 - Refuse a nested unit of work on a snapshot store instead of letting the outer commit discard it.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C32 / memory.py unit_of_work (storage)
- last seen: 2026-11-11T17:57:06Z

### L-007 - Enforce each declared capability at the call that needs it, and run the lacking branch against a stub backend.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C33 / base.py NEEDS (storage)
- last seen: 2026-11-11T17:57:06Z

### L-008 - Prove backend parity through ingest over merge, conflict and candidate writes, not create and drop only.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: storage-ports
- evidence: C25 / contract.py ingest (storage)
- last seen: 2026-11-11T17:57:06Z

### L-009 - When a backend stores timestamps as text, assert the raw stored text is UTC, not only that values round-trip
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: sqlite-backend
- evidence: src/memhub/storage/sqlite.py _ts; C7 (storage)
- last seen: 2026-11-11T18:29:58Z

### L-010 - Prove a rank-then-boost rule with more rows than k and a boosted row ranked below k
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `storage` · harmful: 0
- features: sqlite-backend
- evidence: src/memhub/storage/sqlite.py search; C12 (storage)
- last seen: 2026-11-11T18:29:59Z

### L-011 - Reject a URL whose path is empty after its root, not only an empty remainder
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `connection` · harmful: 0
- features: sqlite-backend
- evidence: src/memhub/connection.py sqlite_path; C2 (connection)
- last seen: 2026-11-11T18:29:59Z

### L-012 - Content-hash ids for resent messages must not depend on where the resent window starts
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `ingestion` · harmful: 0
- features: push-ingestion
- evidence: src/memhub/pipeline/push.py to_interactions; C8 (ingestion)
- last seen: 2026-11-11T20:25:43Z

### L-013 - Prove every row of an event mapping, including the review-pending branch of a new version
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `ingestion` · harmful: 0
- features: push-ingestion
- evidence: src/memhub/memory.py _push_event; C2 (ingestion)
- last seen: 2026-11-11T20:25:43Z

### L-014 - Do not chain != in assertions: compare each variant against the base value
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: push-ingestion
- evidence: tests/test_push.py test_message_ids; C9 (tests)
- last seen: 2026-11-11T20:25:43Z

### L-015 - When removing a default config (preset), rerun the real-model scenarios before deleting: its prompt text carried extraction quality the starter must keep.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `config` · harmful: 0
- features: project-memory
- evidence: AD-034 (config)
- last seen: 2026-11-12T19:05:09Z

### L-016 - A 'no X anywhere in src' check contradicts any check requiring an error message that names X; scope it to exclude the refusal lines when writing checks.
- signal: `spec_deviation` · recurrence: 1 feature(s) · scope: `checks` · harmful: 0
- features: project-memory
- evidence: C31 vs C19 (checks)
- last seen: 2026-11-12T19:05:09Z

### L-017 - Never run two sessions in one worktree: the other's uncommitted files block the clean-tree gate; give each session its own git worktree.
- signal: `gate_fail` · recurrence: 1 feature(s) · scope: `workflow` · harmful: 0
- features: project-memory
- evidence: validate_verification (workflow)
- last seen: 2026-11-12T19:05:09Z

## Quarantined (failed when applied - ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
