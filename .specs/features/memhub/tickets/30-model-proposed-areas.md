# 30 — Areas the model may propose, within a cap

**What to build:** The seed list is a starting point. When no existing area fits, the model may propose a new one,
and the system keeps the list small: near-duplicates merge, new areas are marked as unconfirmed, and there is a
cap.

- The extraction output's `areas` also accepts `{new: {title, description}}` next to `{existing: key}`. The
  prompt says: reuse an existing area whenever one fits; propose a new one only when none does.
- Resolving a new area (in route): embed `title: description` and compare it with the owner's areas. At or above
  `areas.merge_similarity` (default 0.88) the candidate uses the existing area. Otherwise create the area row
  with `proposed = true`.
- Caps: `areas.max_per_user` (25) and `areas.max_per_workspace` (40). At the cap the candidate is dropped as
  `area_cap` and no area is created.
- `areas.open: false` turns proposals off, and then a `{new: …}` is rejected like any invalid output.
- Admin: `memhub areas` shows the `proposed` flag; `memhub areas merge <from> <to>` moves the links of one area to
  another as new versions and archives the source; renaming or confirming (clearing `proposed`) is an ordinary
  `edit`.
- The run summary reports areas created and areas merged, and `area_cap` as a drop reason.

**Blocked by:** 29 — Areas from seeds

**Status:** done

- [ ] AC 23: a proposed area whose embedding is at or above the merge similarity to an existing area merges into it; a lower one creates a row with `proposed = true`
- [ ] At the cap, a proposal is dropped as `area_cap` and no row is created; with `areas.open: false` a proposal is rejected
- [ ] `memhub areas merge` moves every `in_area` link to the target as new versions and archives the source, with no memory left pointing at the archived area
- [ ] The run summary counts areas created, merged and capped
