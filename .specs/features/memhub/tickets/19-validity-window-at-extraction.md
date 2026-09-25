# 19 — Validity window: resolve relative time and set an expiry date

**What to build:** Every memory written by ingestion knows how long it holds. Relative time is resolved into
absolute dates when the memory is extracted, and anything the text doesn't date gets an expiry date from
config. That way short-lived needs stop living forever.

- New columns: `valid_from`, `valid_until` (both nullable) and `durability`, added through the upgrade path
  from ticket 18.
- The transcript sent to the extractor shows each message's date, e.g. `[#2 2026-03-10] user: ...`.
- The extraction output format gains `durability` (`stable | ongoing | temporary`) and optional ISO
  `valid_from` / `valid_until` fields.
- New prompt rules:
  - Resolve relative time against the message date, and never write relative time into `content`:
    - "I'm 18" → "Born around 2008 (18 on 2026-03-10)", `stable`;
    - "next year" → `valid_until` = end of next year;
    - "for 3 months" → `valid_until` 3 months after the message.
  - How to pick durability:
    - `stable`: identity, documents held, past events;
    - `ongoing`: studies, work, where they live;
    - `temporary`: a current need or errand.
- New config:
  - a top-level `ttl` map by durability (`{stable: null, ongoing: 365d, temporary: 30d}`);
  - an optional `ttl` on any type, which the context Episode from ticket 23 will use (`180d`).
- `valid_until` is chosen in this order:
  1. the date from the text;
  2. `observed_at + the type's ttl`;
  3. `observed_at + ttl[durability]`;
  4. `null`.
- A duplicate merge moves `valid_until` to the later of the two values. `null` counts as later than any date,
  so a repeated claim renews its memory.
- `memhub.example.yaml` and the pilot `memhub.yaml` get the new `ttl` keys.

Search still returns expired memories; hiding them is ticket 20.

**Blocked by:** 18 — Memories dated by when the user spoke

**Status:** done

- [x] AC 13: a candidate without a `valid_until` gets `observed_at + ttl` (the type's ttl first, then the ttl for its durability); a `stable` candidate of a type with no ttl gets `null`
- [x] A candidate with an explicit `valid_until` keeps it, whatever the config says
- [x] AC 15 (validity half): merging into an existing memory sets `valid_until` to the later of the two values, with `null` as the latest
- [x] The transcript given to the (fake) extractor shows each message's date
- [x] An invalid `durability` or an unparseable date fails the output validation and is recorded like any other invalid output (not a crash)
- [x] The config loader accepts the durations (`30d`, `365d`, `null`) and rejects unknown durability keys
