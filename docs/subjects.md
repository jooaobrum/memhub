# Subjects: memories about one machine, customer or patient

Many memories are about a **subject**: one machine, one customer, one patient, one repo. Two subjects can look almost the same in writing. "Pump P-101 vibrates above 7 mm/s" and "Pump P-102 vibrates above 7 mm/s" embed as near-duplicates, but they are two facts about two machines.

memhub keeps subjects apart in three steps:

1. **Declare** the subject types in `memory.md`.
2. **Bind** the subject when you write. Your app already knows which subject it is writing about.
3. **Filter** by subject when you search, and **widen** to broader subjects only when you ask for it.

## 1. Declare subject types

In the frontmatter of `memory.md`:

```yaml
---
types:
  fact: builtin
entities:
  machine: isolate                               # memories about different machines never merge
  model: isolate
  patient: {isolate: true, require_filter: true} # only returned when a search names the patient type
  topic: tag                                     # a plain label: it never keeps memories apart
---
```

| Form | Meaning |
| --- | --- |
| `name: isolate` | A subject. Memories about different ids of this type are never compared, merged or judged together. |
| `name: tag` | A label. It is stored and can be filtered on, but it does not keep memories apart. |
| `name: {isolate: true, require_filter: true}` | A guarded subject. A search that doesn't name this type never returns memories tagged with it. |
| `entities: [machine, model]` | A list: every type in it is `isolate`. |

The older `entity_types: [machine]` in `memhub.yaml` still works and means `isolate`. Declaring a type in both places is an error.

### What "isolate" guarantees

Reconcile compares a new memory only with stored memories that have **the same set of isolated subjects**. A memory with no subject has the empty set. This applies on every path:

- **Identical text** about another subject → a new memory, not a merge.
- **Near-duplicate** (similarity ≥ `reconcile.duplicate`) about another subject → a new memory.
- **The judge** is never shown a memory about another subject.
- **A keyed slot** such as `profile.model` holds one value per owner. A value about another subject becomes a `CONFLICT` for a person to review, never a silent overwrite.

## 2. Bind the subject when you write

Pass canonical ids. Resolving names and aliases ("Pump 1", "bomba 101" → `P-101`) is your app's job: do it with your asset registry, CRM or patient system before calling memhub.

```python
P101 = {"type": "machine", "id": "plant-a/line-2/P-101"}
CR32 = {"type": "model", "id": "grundfos-cr-32"}

# a fact you already have (no model call)
memory.add("Bearing replaced after vibration alarm at 7.2 mm/s",
           entities=[P101, CR32], workspace_id="plant-a", user_id="tech-42")

# a conversation: every memory extracted from it is about P-101
memory.add(messages, infer=True, entities=[P101, CR32], workspace_id="plant-a", user_id="tech-42")
```

`entities=` replaces any entity of the same type that the data or the model supplied. If the technician says "same fault as P-102", the memory is still about P-101. Entities of other types that the model extracts are kept, such as `part: 6205`.

## 3. Filter and widen when you search

```python
# only P-101
memory.search("why does it vibrate?", filters={"entities": [P101]}, workspace_id="plant-a", user_id="tech-42")

# P-101 first, then its model, then general knowledge
hits = memory.search("why does it vibrate?", filters={"entities": [P101, CR32]}, widen=True,
                     workspace_id="plant-a", user_id="tech-42")
for h in hits:
    print(h["matched_on"], h["content"])   # "machine:plant-a/line-2/P-101" | "model:grundfos-cr-32" | "general"

# every machine, on purpose (fleet-wide)
memory.search("bearing failures", filters={"entity_type": "machine"}, workspace_id="plant-a", user_id="tech-42")

# listing, newest first
memory.get_all(filters={"entities": [P101]}, workspace_id="plant-a", user_id="tech-42")
```

| `filters` key | Keeps |
| --- | --- |
| `entities: [a, b, ...]` | Memories about any of them. Results are ranked by **tier**: `a`'s first, then `b`'s, each by similarity. A memory about both `a` and `b` appears once, under `a`. |
| `entity_type: "machine"` | Memories about any subject of that type. |
| `types: [...]` | The same as the `types=` parameter. Give it in one place only. |

- **`widen=True`** adds a last tier, `general`: memories about no isolated subject. It needs `filters["entities"]`.
- **`top_k`** cuts across all tiers, so a subject's own memories are never pushed out by better-scoring general ones.
- **`matched_on`** is present on every hit when `filters` is given, and absent otherwise.
- **Unknown keys, unknown types or malformed entities** raise `ValidationError` before any database call.

memhub stores no relation between subjects. You pass the order to widen in, because your registry knows that P-101 is a CR-32.

Without `filters`, search returns what it always did, with one exception: a `require_filter` type. Its tagged memories never appear in a search that doesn't name the type. This also holds for every surface that searches without filters: `MemoryToolkit`, `MemoryMiddleware`, `memhub search` and the MCP server.

## Recipes by domain

| Domain | Subject types | Write | Search |
| --- | --- | --- | --- |
| Industrial maintenance | `machine: isolate`, `model: isolate` | `entities=[machine]` from the work order or alarm | `filters={"entities": [machine, model]}, widen=True` |
| Customer support | `customer: isolate`, `account: isolate` | `entities=[customer]` from the ticket | `filters={"entities": [customer, account]}, widen=True` |
| Healthcare | `patient: {isolate: true, require_filter: true}` | `entities=[patient]` from the encounter | `filters={"entities": [patient]}`, with no `widen` to other patients |
| Software | `repo: isolate`, `org: isolate` | `entities=[repo]` from the working folder | `filters={"entities": [repo, org]}, widen=True` |

## What subjects do not do

- **They don't resolve names to ids.** Pass canonical ids.
- **They don't hold one keyed value per subject.** A keyed slot is one per owner, and another subject's value becomes a conflict. Use a non-keyed type, such as a `fact` tagged with the subject, for per-subject attributes.
- **They don't split memories merged before subjects were declared.** Their history is kept.
- **The toolkit, middleware, CLI and MCP take no filter yet.** They only get the `require_filter` guard.
