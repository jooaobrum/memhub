# Set up memory.md for a project

This is a prompt. Paste it into the coding agent or chat assistant you use, or follow it yourself. It interviews the
project owner, writes `memory.md`, and tests the file on one conversation. The file format is in
[memory.md](memory.md).

---

You are setting up memhub's memory for this project. memhub reads `memory.md`, next to `memhub.yaml`: YAML
frontmatter with `types`, `areas` and `strict`, then a Markdown body with the rules for what to keep. Do the steps in
order, and ask before writing anything.

## 1. Ask

Ask the project owner these questions, one at a time, and wait for each answer:

1. **What should be remembered?** Ask for three or four concrete examples of a memory worth keeping, in their own
   words (for example "Lea saw two Eurasian Hoopoes at the Camargue on 12 November").
2. **What must never be remembered?** Ask for things users say that must stay out (for example "their job, their
   family, the weather").
3. **How does it group?** Ask whether these memories fall into a handful of topics a person would browse, and which.
4. **Who shares it, and who approves it?** Ask whether each kind of memory belongs to one user, or is shared across a
   team or workspace. Then ask which kinds a person must approve before the agent uses them (a diagnosis, a fix, an
   account fact) and which are safe to accept at once (a profile detail, a preference).

## 2. Write

From the answers, write `memory.md`:

- **One type per kind of memory.** Write it as a template whose placeholders are its fields, for example
  `sighting: "Saw {species} at {location}[ on {seen_on?}]"`. Use `{name:int?}` for typed or optional fields, and put
  optional text in `[...]`. Prefer `fact: builtin`, `preference: builtin` or `profile: builtin` when a built-in type
  fits.
- **The long form** (`fields` with a `description`) is for a field whose meaning the extractor could misread, or for a
  shared type (`scopes: [workspace]`).
- **`review:`** from question 4: `review: workspace` (the default: shared memories wait), `none` or `all`, and
  `review: true` / `false` on a type that differs, e.g. `profile: {builtin: true, review: false}`.
- **Areas**, when the owner named topics: one `key: description` line each.
- **`strict: true`** when the owner said the memory must hold their domain only.
- **The body**: start with `# What to remember`, then one sentence on the purpose and one bullet per rule. Include
  every "never" the owner gave.

Show the file to the owner. Change it until they agree, then save it next to `memhub.yaml`, which must contain
`memory: ./memory.md`.

## 3. Test

Write one realistic conversation of five to eight messages that mixes things to keep with things to leave out. Push
it and read the result:

```python
from memhub import Memory

out = Memory().scope(workspace_id="test", user_id="owner", run_id="setup-1").add(messages, infer=True)
print([(e.type, e.content) for e in out.results])
print([(d.content, d.reason) for d in out.dropped])
```

For each drop, check the reason:

| Reason | Change |
| --- | --- |
| `no_area` on something to keep | add an area, or describe an existing one better |
| something kept that should not be | add a "Never" rule to the body |
| `low_score` on something to keep | say in the body why it matters; lower `admission.threshold` in `memhub.yaml` only as a last resort |

Change one thing at a time, then push the same conversation as a new run (`run_id="setup-2"`). Stop when the result
matches what the owner said in step 1, and show it to them.
