# Search filters

`filters=` narrows `Memory.search` and `Memory.get_all` by subject, subject type, memory type, area, time and text. The same filter works in `memhub search` / `memhub list` (`--filter`) and in the `search_memory` tool of `MemoryToolkit`, `MemoryMiddleware` and `memhub mcp`.

```python
# every machine whose id starts with PRD_
memory.search("vibration", filters={"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}, **ids)

# a memory about PRD_1 and PRD_2 together
memory.search("vibration", filters={"AND": [{"entities": [PRD_1]}, {"entities": [PRD_2]}]}, **ids)

# PRD_1 or any line, in two areas, seen since October, mentioning E47
memory.search("vibration", filters={
    "OR": [{"entities": [PRD_1]}, {"entity_type": "line"}],
    "areas": {"in": ["bearings", "lubrication"]},
    "observed_at": {"gte": "2026-10-01"},
    "content": {"contains": "E47"},
}, **ids)
```

## Keys

Top-level keys are ANDed: a memory is kept when it passes every key.

| Key | Takes | Keeps |
| --- | --- | --- |
| `entities` | `{"type", "id"}`, or a list of them (any of) | Memories naming that subject. `id` is a string (exact) or one operator below. The `type` must be declared in `memory.md` (`entities:`). |
| `entity_type` | `"line"` or `{"in": ["machine", "line"]}` | Memories naming any subject of that type. |
| `types` | `["fact", "preference"]` | Memories of those memory types. The same as the `types=` parameter; give it in one place only. |
| `areas` | `"bearings"` or `{"in": ["bearings", "lubrication"]}` | Memories linked to your area (yours or the workspace's) whose key or title is that name, ignoring case. A name no area has keeps nothing. |
| `observed_at` | `{"gte": ..., "lte": ...}`, one or both | Memories observed in that range. |
| `valid_until` | `{"gte": ..., "lte": ...}`, one or both | Memories expiring in that range. A memory that never expires passes every `gte` and no `lte`. |
| `content` | `{"contains": "E47"}` | Memories whose text holds it, ignoring case. |

## Operators

| Operator | On | Meaning |
| --- | --- | --- |
| `in` | subject ids, `entity_type`, `areas` | One of the listed values. |
| `prefix` | subject ids | Starts with the text. Case counts; `_` and `%` are plain characters. |
| `contains` | subject ids, `content` | Holds the text. Case counts on ids, not on content. |
| `gte` | `observed_at`, `valid_until` | On or after. A date (`"2026-10-01"`) starts at 00:00 UTC. |
| `lte` | `observed_at`, `valid_until` | On or before. A date keeps that whole day (UTC); a datetime compares exactly. A datetime with no timezone is UTC. |

## Combinators

| Combinator | Takes | Keeps |
| --- | --- | --- |
| `AND` | a non-empty list of filters | Memories passing every one. |
| `OR` | a non-empty list of filters | Memories passing at least one, each once. |
| `NOT` | one filter | Memories not passing it. A comparison with a missing value fails, so its `NOT` passes. |

## Ranking, `widen` and `matched_on`

- A top-level `entities` list is ranked by **tier**: the first item's memories first, then the next item's, each by similarity. An item with an operator (`prefix`, `in`, `contains`) is one tier. Every other top-level key applies to each tier.
- **`widen=True`** adds a last tier, `general`: memories about no isolated subject that pass the other top-level keys. It needs a top-level `entities`, and takes no `AND` / `OR` / `NOT` at the top level.
- **`matched_on`** names the subject a hit matched (`"machine:PRD_2"`), or `general`. It is present when the filter names a subject, and absent otherwise.
- A `require_filter` subject type is returned only when a filter names it outside a `NOT` (see [subjects.md](subjects.md)).


## Hybrid search

Embeddings are weak on codes and ids (`E47`, `PRD_1`, `6205`, `INC-4821`, `v2.3.1`): a query that is only a code has
almost no meaning to embed, and `E47` and `E74` look alike. So `search` runs a keyword leg next to the vector leg and
fuses the two. It is on by default.

- **What counts as a keyword.** Every word of the query, minus words shorter than 2 characters, minus the stopwords
  of Italian, French, English and Portuguese (the Snowball lists), minus the project's own generic words in
  `search.stopwords`. Case and accents are ignored (`préfère` matches `prefere`). There is no stemming.
- **What matches.** A keyword matches a whole word of the memory's `content`: `E47` does not match `E470`, `PRD_1`
  does not match `PRD_10`. `-`, `_`, `/` and `.` inside a code keep it whole (`P-101`, `6205-2RS`, `v2.3.1`).
- **Generic words drop out by themselves.** A keyword found in more than `search.keyword_max_df` of the memories the
  search may return (half, by default) is ignored for that search, so a word every memory of the project holds
  ("pump" in a pump plant) does not push the right memory down. Rarer keywords weigh more (IDF).
- **Fusion.** Each leg ranks its hits; a memory scores `vector_weight / (60 + vector rank) + keyword_weight / (60 +
  keyword rank)`. Subject tiers keep their order: the two legs are fused inside each tier. With no keyword left, the
  result is the vector search's.
- **`matched_by`** on each hit says which leg found it: `vector`, `keyword` or `both`. The `search_memory` tool of
  `MemoryToolkit` and `memhub mcp` shows it too.
- Only active, non-stale memories of the caller and the workspace are matched, with the same filters and the same
  `require_filter` guard as the vector leg. Nothing is stored for it: no index, no migration.

| Key | Default | What it does |
| --- | --- | --- |
| `search.hybrid` | `true` | `false`: the vector search alone, as before. `Memory.search(..., hybrid=True/False)` overrides it per call |
| `search.vector_weight` | `1.0` | weight of the vector leg (0 or more) |
| `search.keyword_weight` | `1.0` | weight of the keyword leg (0 or more); not both 0 |
| `search.keyword_max_df` | `0.5` | a keyword in more than this share of the searchable memories is ignored (above 0, at most 1) |
| `search.stopwords` | `[]` | the project's generic words, never a keyword |

```yaml
search:
  keyword_weight: 1.5               # codes matter more than meaning in this project
  stopwords: [machine, máquina, macchina, problema]
```

`scripts/bench_search.py --codes` prints the rank of the expected memory for a set of code-style queries, with
hybrid on and off.

## Errors

An unknown key or operator, an operator on a key that does not take it, an undeclared subject type, an empty `AND` / `OR` / `in`, an empty `prefix` / `contains`, or a date that is not ISO 8601 raises `ValidationError` before any database or model call. The CLI prints the reason and exits 1; the `search_memory` tool returns it as an error (`{"error": ...}`, or `invalid filters: ...` from the middleware).

## CLI

`--entity TYPE:ID` is the short form of a top-level `entities` list; repeat it to rank several subjects in that order.

```bash
memhub search "vibration" --filter '{"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}'
memhub search "vibration" --entity machine:PRD_1 --entity machine:PRD_2
memhub list --filter '{"areas": "bearings", "observed_at": {"gte": "2026-10-01"}}'
```
