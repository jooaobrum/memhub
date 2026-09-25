# 05 — Workspace review queue: approve and reject

**What to build:** Shared knowledge is published only when a person approves it. A `workspace_admin` can review workspace-scope candidates:

- `memhub queue [--ws ..]` lists workspace candidates, with conflicts (rows that have `conflicts_with`) first.
- `memhub approve <id> [--resolve keep_old|replace|keep_both] [--note ..]` moves `candidate → active`, sets `verified=true` and fills `reviewed_by`, `reviewed_at` and `review_note`. For a conflicting candidate, the reviewer must choose a resolution:
  - `keep_old`: this row becomes rejected.
  - `replace`: the old active row becomes archived.
  - `keep_both`: both stay active.
- `memhub reject <id> [--note ..]` moves `candidate → rejected`.
- Approval requires a role listed in `roles.approve_workspace`.
- Promoting a user memory to workspace scope always creates a workspace candidate in this queue.

Conflict rows can be seeded directly in tests; ticket 10 produces them for real.

**Blocked by:** 03 — Semantic search with citable results; 04 — Versioned edit, archive and delete by id

**Status:** done

- [ ] AC 6: a workspace-scope candidate is not returned by search until an actor with `workspace_admin` approves it, and after approval it is returned with `verified=true`
- [ ] An actor without the approving role is refused
- [ ] Each `--resolve` option produces the documented status changes, and approving a conflict without `--resolve` is refused
- [ ] The queue orders conflicts first
- [ ] Promoting a user memory creates a workspace candidate and leaves the user memory unchanged
