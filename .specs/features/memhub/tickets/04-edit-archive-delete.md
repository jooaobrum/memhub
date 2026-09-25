# 04 — Versioned edit, archive and delete by id

**What to build:** A reviewer or memory owner can change an existing memory without losing its history:

- `memhub edit <id> --file fields.json` validates the fields against the type, inserts version N+1 as `active` and sets version N to `superseded`, in one transaction. `verified=true` when an admin edits. An edit coming from the agent or extractor creates a new `candidate` instead.
- `memhub archive <id>` moves the row from `active` to `archived`.
- `memhub delete <id>` hard-deletes a row. This is only allowed when `status=rejected`, or when the actor owns the user-scope row.
- Only the owner can edit or delete their user-scope memories. Every operation takes an `actor` (id + roles).

**Blocked by:** 02 — Add memories by hand and list them

**Status:** done

- [ ] AC 8: an edit produces version N+1 `active` and version N `superseded` atomically, and the partial unique index guarantees that no `memory_id` ever has two active versions (tested, including a concurrent or failed edit)
- [ ] An edited payload that fails validation writes nothing
- [ ] Archive works only on active rows
- [ ] Delete is refused for active or candidate rows unless the actor owns the user-scope row
- [ ] A non-owner cannot edit or delete another user's memory
