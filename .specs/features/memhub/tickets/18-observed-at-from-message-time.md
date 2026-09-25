# 18 — Memories dated by when the user spoke, not by ingestion

**What to build:** Every memory knows when its evidence was said, so that later tickets can reason about time.
In pilot run 3 every row carried the ingestion date (2026-09-24), which made "next year" impossible to resolve.

- `memhub init` can upgrade an existing ledger: it adds new columns with `ADD COLUMN IF NOT EXISTS`, so running
  it again on a v1 ledger is safe. This is the upgrade path the later v1.1 tickets reuse for their columns. It
  starts with `observed_at`. Existing rows get `observed_at = created_at`.
- Each evidence entry written by ingestion carries `observed_at`, the timestamp of its message.
- Each memory's `observed_at` is the newest timestamp among its evidence messages. It is never the ingestion
  time.
- Merging evidence into an existing memory moves its `observed_at` to the later of the two values.
- `memhub add` sets `observed_at` to now, unless the input file gives one.
- `memhub list` and `memhub search` show `observed_at`, and search results from the service include it.

**Blocked by:** None — can start immediately

**Status:** done

- [x] AC 12: a memory ingested from messages dated in the past has `observed_at` equal to its newest evidence message's timestamp, and each evidence entry carries its message's timestamp
- [x] Running `memhub init` on a ledger created before this ticket adds the column without losing rows, and running it twice is a no-op
- [x] A duplicate merge from a later message moves `observed_at` forward, and one from an earlier message leaves it unchanged
- [x] `memhub add` without a date stores the current time; with a date in the file it stores that date
- [x] `list` and `search` output show `observed_at`
