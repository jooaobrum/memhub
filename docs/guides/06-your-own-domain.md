# Tune memhub to your own domain

Most projects need a narrow memory: one that keeps only what matters to the domain and ignores everything else people
say. In memhub that is one file, `memory.md`. This guide builds one for a birdwatching club app. It stores bird
species knowledge and sightings, and nothing about the people.

`memory.md` has three levers:

- **types**: what a memory looks like;
- **areas**: where a memory is filed, and, with `strict`, whether anything outside them is kept;
- **the body**: what the extractor is told to keep.

## 1. Start from a working config

```bash
memhub init --project birds --db sqlite:///$HOME/.memhub/birds.db \
  --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini
```

This writes `memhub.yaml` (database and models) and a starter `memory.md`. Replace the starter with your own.

## 2. Write memory.md

```markdown
---
strict: true
types:
  sighting:     "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"
  species_note: "{common_name}: {note}"
areas:
  raptors:             hawks, eagles, falcons, kites, vultures, ospreys
  owls:                owls of all kinds
  waterbirds:          ducks, geese, swans, herons, egrets, grebes, rails
  shorebirds_seabirds: waders, gulls, terns, auks, gannets
  songbirds:           "passerines: warblers, finches, thrushes, tits, larks"
  corvids:             crows, ravens, magpies, jays
  other_birds:         pigeons, woodpeckers, hoopoes, kingfishers, cuckoos
---
# What to remember

This memory is only about bird species.

- Keep what the user states about a specific bird species (identification, behaviour, call, habitat, plumage) as a
  `species_note`.
- Keep each bird the user says they saw as a `sighting`, with the place and the absolute date.
- Use the common English name of the species.
- Never store anything else: not the user's job, family, travel, tools, opinions about apps, or the weather.
- A bird fact that only the assistant said is not a memory. A question about a bird is not a memory.
```

What each part does:

- **Types.** Each type is one line. Its placeholders are the fields: `{seen_on?}` is optional, `{count:int?}` is an
  optional number. A `[...]` section is written only when its fields have a value, so a sighting with no date reads
  `Saw Grey Heron at the canal`. To give a field a description for the model, use the long form
  ([docs/memory.md](../memory.md)).
- **Areas.** Each area is `key: description`; the title comes from the key (`other_birds` becomes "Other birds"). Put
  a description that contains a colon in quotes.
- **`strict: true`.** The model may not invent areas, and every memory needs one. A candidate that fits no bird group
  is dropped with the reason `no_area`. The areas become a domain filter, and they also organise the memory:
  `memhub areas --ws birdclub --user lea` gives the counts per group.
- **The body.** It is the extractor's brief. Write it as you would brief a new colleague.

## 3. Test with a realistic, messy conversation

```python
from memhub import Memory

me = Memory().scope(workspace_id="birdclub", user_id="lea", run_id="chat-1")
out = me.add([
  {"role": "user", "content": "Morning! I'm a nurse, just finished a night shift, and the coffee here is terrible."},
  {"role": "user", "content": "Yes! I saw two Eurasian Hoopoes at the Camargue salt flats today, first time ever."},
  {"role": "user", "content": "I noticed the Hoopoe raises its crest when it lands, and its call is a soft 'oop-oop-oop'."},
  {"role": "user", "content": "Also a Grey Heron stood totally still for ten minutes in the canal before striking a fish."},
  {"role": "user", "content": "By the way I'm moving to Lyon next month and I use an iPhone 15. What's the best binocular?"},
  {"role": "assistant", "content": "8x42 is a great all-round choice. Peregrine Falcons, by the way, dive at over 300 km/h."},
], infer=True)
for e in out.results:
    print(e.type, e.content)
```

Stored (one run; the wording is the model's):

```
sighting      Saw Eurasian Hoopoe at Camargue salt flats on 2026-11-12 (count 2)     -> Other birds
species_note  Eurasian Hoopoe: raises its crest when it lands; call a soft 'oop-oop-oop'  -> Other birds
sighting      Saw Grey Heron at the canal on 2026-11-12                               -> Waterbirds
```

Not stored: the job, the coffee, the move to Lyon, the phone, the binocular question, and the falcon fact that only
the assistant said.

## 4. Adjust with the drops

Every candidate that is not stored comes back in `out.dropped`, or in `memhub runs` for a scheduled ingest, with a
reason:

| Reason | Means | Lever |
|---|---|---|
| `no_area` | fits no area | add an area, or describe one better; or accept it as filtered out |
| `low_score` | below `admission.threshold` (utility, evidence, novelty) | say in the body why such memories matter; lower the threshold in `memhub.yaml` only as a last resort |
| `unsupported_claim`, `assistant_only` | not grounded in what the user wrote | expected; add a "never" rule to the body if frequent |
| `question_only` | the user only asked | expected |

Change one lever at a time and rerun the same conversation on a fresh ledger. `memhub config show` shows each setting
and where it came from: `memory.md`, `memhub.yaml`, the environment or code. To write a `memory.md` for another domain
with help, use [docs/memory-setup.md](../memory-setup.md).
