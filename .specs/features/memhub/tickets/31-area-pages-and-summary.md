# 31 — Area pages with a derived summary

**What to build:** A person's memories in an area can be read as one page: a title, a two-sentence summary, the
dated details, and when it was last updated. This is the "fewer things to manage" view. It is assembled from the
rows, so evidence, expiry and review per claim keep working, and the summary is derived text, never evidence.

- `MemoryService.page(owner, area)` returns `{title, summary, details, last_updated}`. `details` are the active,
  non-stale rows linked to the area (`in_area`), newest `observed_at` first, capped at `areas.page_max_items`
  (15). `last_updated` is the newest `observed_at` among them. No stored copy, so nothing to keep in sync.
- `memhub page --user <id> [--area <key|title>]` prints one page, or every page of the user.
- Summary: after the last segment of a run, every area that gained or changed a linked row gets one call to the
  judge model. The prompt holds only the area title and the `content` of its active linked rows, and asks for two
  sentences at most, adding nothing that is not in the rows. The result is stored as a new version of the area
  row with `created_by = "summarizer"`, labelled auto-summary. No call when the area has no active rows, when
  its row set is unchanged since the last summary, or on `--dry-run`.
- The run summary reports summaries written and their tokens.
- A summary is never used as evidence: it is excluded from grounding, reconcile and search results as a claim
  (search may return the area row as a page header, marked auto-summary).

**Blocked by:** 29 — Areas from seeds

**Status:** done

- [ ] AC 24: `page` returns the title and summary, the active non-stale linked rows newest first, and `last_updated` equal to the newest `observed_at`
- [ ] AC 25: a run that adds a row to an area writes exactly one new summary version for it; a run that changes nothing writes none; an area with no active rows makes no call
- [ ] The summary prompt contains only the rows' content (checked with a fake judge), and `--dry-run` writes nothing
- [ ] `memhub page --user <id>` prints all of a user's pages, each marked auto-summary
