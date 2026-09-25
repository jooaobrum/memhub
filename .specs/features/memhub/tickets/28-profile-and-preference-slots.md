# 28 — Profile and Preference slots

**What to build:** Who the person is (Profile) and how they want the agent to answer (Preference) are stored as
a small set of named slots, one active row per slot. The same identity fact can no longer be stored many times
(run 3 stored "Is Brazilian and lives in Grenoble" about 8 times), and a change such as a new city becomes an
update of one row.

- New built-in type `Profile` (`key`), joining `Preference` (`key`, exists). A type marked `keyed: true` in
  config lists its allowed `keys`, each with a one-line description: Habitantes Profile `nationality, city, age,
  residence_status, studies, work, family`; Preference `language, scope, style, detail`. Another project lists
  its own keys in its own config.
- Reconcile handles every keyed type the same way (today it only special-cases `preference`): the active row
  with the same (owner, type, key) is superseded by the candidate as a new version, or merged when the text is
  the same. No similarity search is needed for a keyed type. The rules for older, verified, immutable and
  complementary values are ticket 34; this ticket only does the plain replace-by-key.
- The extractor schema offers `key` as an enum of the configured keys, and the prompt describes each key. Person
  attributes go to `profile`, response wishes go to `preference`, and anything that matches no key is not stored
  as either. Profile and Preference have no area.
- A candidate whose key is not in the list is dropped as `unknown_key` (defence in depth, in ground).
- A partial unique index on (owner, type, key) for active keyed rows makes a second active row impossible.
- `memhub list` shows the key. `memhub add --type profile` validates the key too.
- The Habitantes `memhub.yaml` turns on `profile` and `preference` with their keys and updates the extraction
  instructions for the two slot kinds (keep the v4 rules about durable, first-person statements).

**Blocked by:** 27 — Conservative defaults

**Status:** done

- [x] AC 21: an unknown key is dropped as `unknown_key`; a valid key supersedes the active row with the same (owner, type, key) as a new version
- [x] Two segments saying the user is Brazilian produce one active `nationality` row (evidence accumulates); a later "moved to Paris" supersedes the `city` row and the Grenoble version stays in history
- [x] The database rejects a second active row for the same (owner, type, key)
- [x] `memhub add --type profile` with a key outside the config fails with the validation error and writes nothing
- [x] Habitantes config loads with the slot keys; the extractor schema shows the keys as an enum
