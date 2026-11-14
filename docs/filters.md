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

## Errors

An unknown key or operator, an operator on a key that does not take it, an undeclared subject type, an empty `AND` / `OR` / `in`, an empty `prefix` / `contains`, or a date that is not ISO 8601 raises `ValidationError` before any database or model call. The CLI prints the reason and exits 1; the `search_memory` tool returns it as an error (`{"error": ...}`, or `invalid filters: ...` from the middleware).

## CLI

`--entity TYPE:ID` is the short form of a top-level `entities` list; repeat it to rank several subjects in that order.

```bash
memhub search "vibration" --filter '{"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}'
memhub search "vibration" --entity machine:PRD_1 --entity machine:PRD_2
memhub list --filter '{"areas": "bearings", "observed_at": {"gte": "2026-10-01"}}'
```
