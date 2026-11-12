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
