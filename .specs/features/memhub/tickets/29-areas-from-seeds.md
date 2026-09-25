# 29 — Areas from seeds: Facts belong to an area

**What to build:** Every Fact is about a topic, and a Fact that fits no topic is dropped. Habitantes lists its 19
areas (Visto & Residência, Moradia & CAF, …) in config. An area is a row of type `area`, and Facts point to it
with an `in_area` link, so there is no new table. The joke question stored as a fact in run 5 fits no area and is
dropped.

- New built-in type `Area` (`title`, `description`, `summary`, `proposed`). Config `areas.seeds` lists key, title,
  icon and description for each seed area. The 19 Habitantes areas go in `memhub.yaml`, each with a one-line
  description that tells the model what belongs there.
- A seed becomes a row for a user only when that user's first memory lands in it, exactly once.
- A type can set `area: required` (Fact in Habitantes) or `area: optional`. The extraction output gains `areas`
  (one to three existing area keys). For `required`, a candidate with no area is dropped as `no_area`. The prompt
  lists the areas with their descriptions and asks the model to pick the ones that fit.
- Route writes `links = [{kind: "in_area", memory_id: <area memory_id>}]` (uses the `links` column and
  `add_link` from ticket 23). Merge and supersede append the link if it is not already there.
- Reconcile compares only rows that share at least one area with the candidate (rows without areas are compared
  as before).
- Search results carry `areas` (titles). Search boosts rows in the query's best-matching area and never filters
  by it. The best area comes from the nearest area description by embedding, or from a caller argument.
- `memhub list --area <key>` and `memhub areas [--user]` list an owner's areas with counts.
- `Fact` in Habitantes gets `area: required`.

**Blocked by:** 26 — Remove the automatic context Episode

**Status:** done

- [ ] AC 22: an admitted Fact has one to three `in_area` links to area rows of its owner; a Fact with no area is dropped as `no_area`
- [ ] The first Fact of a user in a seed area creates that user's area row once; a second Fact reuses it
- [ ] Reconcile does not compare (and so never merges or conflicts) a housing Fact with a visa Fact
- [ ] Search returns `areas`, boosts the query's best area and still returns matches outside it
- [ ] `memhub areas --user <id>` lists the user's areas with counts
