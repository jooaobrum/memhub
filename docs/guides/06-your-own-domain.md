# Tune memhub to your own domain

The presets cover a personal assistant, customer support and maintenance. Most projects need something narrower: a
memory that keeps **only** what matters to the domain and ignores everything else people say. This guide builds one
for a birdwatching club app. It stores bird species knowledge and sightings, and nothing about the people.

The three levers are **types** (what a memory looks like), **areas** (where it is filed, and whether anything outside
them is kept) and **instructions** (what the extractor is told to keep).

## 1. Start from a working config

```bash
memhub init --project birds --db sqlite:///$HOME/.memhub/birds.db \
  --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini
```

## 2. Replace the preset's types with your own

Add to `memhub.yaml`:

```yaml
types:
  fact: null            # `<type>: null` removes a preset type: only the bird types below remain
  profile: null
  preference: null
  episode: null
  case: null
  skill: null
  species_note:
    fields:
      common_name: {type: str, description: "the species' common name, e.g. Eurasian Hoopoe"}
      scientific_name: {type: "str?", description: binomial name, only if said}
      note: {type: str, description: "what the user observed or knows: behaviour, call, habitat, plumage"}
    content_template: "{common_name}: {note}"
    area: required
    scopes: [user]
  sighting:
    fields:
      species: {type: str, description: common name of the bird seen}
      location: {type: str, description: where it was seen}
      seen_on: {type: "str?", description: "ISO date of the sighting, from the message date"}
      count: {type: "int?", description: how many individuals}
    content_template: "Saw {species} at {location} on {seen_on} (count {count})"
    area: required
    scopes: [user]
```

Field types are `str`, `int`, `float`, `bool`, `list[str]` and `list[int]`, and a trailing `?` makes a field optional.
The model sees each `description`. For validation logic, point `class:` at a Python class instead.

## 3. Close the areas

```yaml
areas:
  open: false           # the model must pick from this list; it cannot invent areas
  seeds:
    - {key: raptors, title: Raptors, description: "hawks, eagles, falcons, kites, vultures, ospreys"}
    - {key: owls, title: Owls, description: owls of all kinds}
    - {key: waterbirds, title: Waterbirds, description: "ducks, geese, swans, herons, egrets, grebes, rails"}
    - {key: shorebirds_seabirds, title: Shorebirds & Seabirds, description: "waders, gulls, terns, auks, gannets"}
    - {key: songbirds, title: Songbirds, description: "passerines: warblers, finches, thrushes, tits, larks"}
    - {key: corvids, title: Corvids, description: "crows, ravens, magpies, jays"}
    - {key: other_birds, title: Other birds, description: "pigeons, woodpeckers, hoopoes, kingfishers, cuckoos"}
```

With `area: required` and a closed list, a candidate that fits no bird group is dropped (`no_area`). The areas become
a domain filter, and they also organise the memory: `memhub areas --ws birdclub --user lea` gives counts per group.

## 4. Tell the extractor what to keep

```yaml
extraction:
  instructions: |
    This memory is ONLY about bird species. Keep what the user states about a specific bird species (identification,
    behaviour, call, habitat, plumage) as `species_note`, and each bird the user says they saw as `sighting` with the
    place and the absolute date. Nothing else is ever stored: not the user's job, family, travel, tools, opinions about
    apps or the weather, and not a bird fact that only the assistant said. A question about a bird is not a memory.
    Use the common English name of the species. Every candidate gets exactly one area from the list.
```

## 5. Test with a realistic, messy conversation

```python
me = Memory().scope(workspace_id="birdclub", user_id="lea", run_id="chat-1")
out = me.add([
  {"role": "user", "content": "Morning! I'm a nurse, just finished a night shift, and the coffee here is terrible."},
  {"role": "user", "content": "Yes! I saw two Eurasian Hoopoes at the Camargue salt flats today, first time ever."},
  {"role": "user", "content": "I noticed the Hoopoe raises its crest when it lands, and its call is a soft 'oop-oop-oop'."},
  {"role": "user", "content": "Also a Grey Heron stood totally still for ten minutes in the canal before striking a fish."},
  {"role": "user", "content": "By the way I'm moving to Lyon next month and I use an iPhone 15. What's the best binocular?"},
  {"role": "assistant", "content": "8x42 is a great all-round choice. Peregrine Falcons, by the way, dive at over 300 km/h."},
], infer=True)
```

Stored (one run; the wording is the model's):

```
sighting      Saw Eurasian Hoopoe at Camargue salt flats on 2026-11-12 (count 2)     -> Other birds
species_note  Eurasian Hoopoe: raises its crest when it lands; call a soft 'oop-oop-oop'  -> Other birds
sighting      Saw Grey Heron at the canal on 2026-11-12 (count 1)                     -> Waterbirds
```

Not stored: the job, the coffee, the move to Lyon, the phone, the binocular question, and the falcon fact that only
the assistant said.

## 6. Adjust with the drops

Every candidate that is not stored comes back in `out.dropped` (or in `memhub runs` for a scheduled ingest) with a
reason:

| Reason | Means | Lever |
|---|---|---|
| `low_score` | below `admission.threshold` (utility, evidence, novelty) | lower the threshold for a narrow domain: `memhub config set admission.threshold 0.6` |
| `no_area` | fits no area | add an area, or accept it as filtered out |
| `unsupported_claim`, `assistant_only` | not grounded in what the user wrote | expected; tighten instructions if frequent |
| `question_only` | the user only asked | expected |

In this example the preset's threshold (0.75) dropped the heron behaviour note as `low_score`, and 0.6 kept it.
Change one lever at a time and rerun the same conversation on a fresh ledger. `memhub config show` shows each setting
and where it came from: preset, yaml, environment or code.
