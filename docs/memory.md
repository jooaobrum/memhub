# memory.md: what your project remembers

memhub ships no memory types of its own choosing for your domain. Your project says what it remembers in one file
it owns, `memory.md`, next to its `memhub.yaml`:

```yaml
# memhub.yaml
project: birds
db: sqlite:///birds.db
memory: ./memory.md        # relative to this yaml
```

`memhub init` writes both files. To write `memory.md` with help, follow the prompt in
[memory-setup.md](memory-setup.md).

## The file

A `memory.md` has two parts:

- **Frontmatter** between two `---` lines (YAML). It may hold only `types`, `areas` and `strict`, because database,
  models and sources stay in `memhub.yaml`.
- **Body**: Markdown that tells the extractor what to keep and what never to keep. It becomes
  `extraction.instructions`. When the body is empty, memhub's default instructions apply.

A file with no frontmatter is all body, and the starter types apply.

Each key lives in one place. If `memhub.yaml` also sets `types`, `areas` or `extraction.instructions`, loading stops
with `types is set in both memhub.yaml and memory.md: keep one`.

The memory file can also be named with `MEMHUB_MEMORY=./memory.md`, or from code with
`Memory(config={"memory": "./memory.md"})`. In both cases the path is relative to the working directory.
`memhub config show` prints `memory.md` as the source of every setting the file gives.

## Types: the short forms

A type is one line.

**A template.** Its placeholders are the type's fields, and the line is how each memory reads:

```yaml
types:
  sighting: "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"
```

| Placeholder | Field |
| --- | --- |
| `{species}` | a required text field |
| `{seen_on?}` | an optional text field |
| `{count:int}`, `{count:int?}` | a typed field: `str`, `int`, `float`, `bool`, `list[str]` or `list[int]` |
| `[ on {seen_on?}]` | an optional section: written only when its fields have a value |
| `[[`, `]]` | a literal `[` or `]` |

With `seen_on` empty and `count` 2, the memory reads `Saw Grey Heron at the canal (count 2)`.

**A built-in type.** `fact`, `preference`, `profile`, `episode`, `case` and `skill` come with their own fields and
rules:

```yaml
types:
  fact: builtin
  preference: builtin
```

## Types: the long form

Use the long form when a field needs a description for the extractor, or when a type needs more options. Any type
can be written this way, and both forms can be mixed in one file.

```yaml
types:
  account:
    fields:
      company: {type: str, description: "the customer's company"}
      plan: {type: "str?", description: "subscription plan, if said"}
    content_template: "{company}[ - plan {plan}]"
    scopes: [workspace]        # shared across the workspace; waits for review
  preference: {class: "memhub.types:Preference", retrieval: always, keyed: true, keys: {language: "language of the answers"}}
  incident: {class: "my_types:Incident"}    # a Python class next to memhub.yaml
```

| Option | Means |
| --- | --- |
| `fields` / `class` | the schema: fields declared here, or a `MemoryBase` subclass |
| `content_template` | how `content` is written from the fields |
| `scopes` | `user`, `workspace` or both (default: the project's `scopes`) |
| `retrieval` | `search` (default), `always` (in every prompt), `index_then_load` |
| `keyed`, `keys` | one active row per key (a new value replaces the old one) |
| `review` | `false`: a workspace memory is active at once, without review |
| `extract`, `on_remember` | whether extraction proposes it, and whether a `/remember` order may |
| `area` | `required` or `optional` |
| `ttl`, `max_active`, `type_prior` | lifetime, cap per owner, weight in the admission score |

## Areas

Areas group memories into topics, such as an owner's "Waterbirds" or "Housing". In the short form, each area is
written as `key: description`, and the title comes from the key (`other_birds` becomes "Other birds"):

```yaml
areas:
  raptors:     hawks, eagles, falcons
  other_birds: pigeons, woodpeckers, hoopoes
```

The long form gives a title and an icon of your own, plus the area settings:

```yaml
areas:
  open: false
  seeds:
    - {key: housing, title: "Housing", icon: "🏠", description: "renting, lease, deposit"}
```

## strict: only your domain

`strict: true` turns the memory into a domain filter:

- the model may not invent areas;
- every type you list needs an area.

A candidate that fits no area is dropped with the reason `no_area`. A type that sets `area: optional` keeps it.
`strict` leaves the admission threshold unchanged.

## Starter types

`memhub init` writes this file:

```markdown
---
types:
  fact: builtin
  preference: builtin
  profile: builtin
---
# What to remember

Durable facts about the user and their work, what they prefer, and who they are.
Never store secrets, one-off questions, or something only the assistant said.
```

When neither the memory file nor any other setting lists a type, memhub uses these three types, plus its own `area`
and `term`.

## A worked example

A birdwatching club's app should remember bird knowledge and sightings, and nothing about its members:

```markdown
---
strict: true
types:
  sighting:     "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"
  species_note: "{common_name}: {note}"
areas:
  raptors:     hawks, eagles, falcons, kites, vultures, ospreys
  owls:        owls of all kinds
  waterbirds:  ducks, geese, swans, herons, egrets, grebes, rails
  songbirds:   warblers, finches, thrushes, tits, larks
  other_birds: pigeons, woodpeckers, hoopoes, kingfishers, cuckoos
---
# What to remember

Only bird species knowledge and the user's sightings.

- A sighting has the place and an absolute date.
- Use the common English name of the species.
- Never store anything about the person: job, family, travel, devices, opinions.
- A bird fact only the assistant said is not a memory. A question is not a memory.
```

Push one realistic conversation and read what came back:

```python
from memhub import Memory

out = Memory().scope(workspace_id="club", user_id="lea", run_id="chat-1").add(messages, infer=True)
[(e.type, e.content) for e in out.results]     # what was written
[(d.content, d.reason) for d in out.dropped]   # what was not, and why
```

[guides/06-your-own-domain.md](guides/06-your-own-domain.md) walks through this example step by step.
