# Field-service agents that share fixes

A technician on a rooftop finds that fault E47 on a Carrier unit was caused by a clogged filter drier. Next week a
colleague meets the same fault on another site. Without memory, that colleague starts the diagnosis from zero. With
shared, unreviewed memory, they might get one technician's guess as if it were fact.

memhub sits in between. A fix one technician reports becomes a **candidate** for the whole team. A supervisor approves
it, and from then on every technician's assistant finds it.

## The setup

The memory keeps workspace-scoped knowledge about machines, in one `incident` type (equipment, fault code, fix). A
workspace is a company or a region, and each technician is a user. Put this `memory.md` next to your app:

```markdown
---
types:
  incident:
    fields: {equipment: str, fault_code: str, fix: str}
    content_template: "{equipment} fault {fault_code}: {fix}"
    scopes: [workspace]      # shared by the team, after a supervisor approves it
    on_remember: true        # a `/remember` order may record one
---
# What to remember

Maintenance assistant. Keep durable knowledge about machines: model, location, known quirks, and fixes that worked
(an `incident`). Never keep questions, greetings or measurements that only describe the moment.
```

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from memhub import Memory

m = Memory(db="postgresql://…",                    # SQLite works too for a pilot
           llm=ChatOpenAI(model="gpt-4.1-mini"),
           embedder=OpenAIEmbeddings(model="text-embedding-3-small"),
           config={"project": "field", "scopes": ["workspace"], "entity_types": ["machine"], "memory": "./memory.md"})
m.init()
```

## 1. A technician solves a job and says `/remember`

```python
marco = m.scope(workspace_id="acme-hvac", user_id="tech-marco", run_id="job-1042")
out = marco.add([
    {"role": "user", "content": "On site at Riverside Mall. Carrier 50XC rooftop unit #3 shows fault E47 again."},
    {"role": "assistant", "content": "E47 is a low-pressure lockout. Check the refrigerant charge and the liquid-line filter drier."},
    {"role": "user", "content": "Charge was fine. The filter drier was clogged, replaced it and the unit restarted, no E47 after 30 minutes."},
    {"role": "user", "content": "/remember this fix"},
], infer=True)
print([(e.event, e.status, e.content) for e in out.results])
# [('PENDING', 'candidate', 'Carrier 50XC rooftop unit #3 fault E47: Replaced clogged liquid-line filter drier'),
#  ('MERGE',   'candidate', 'Carrier 50XC rooftop unit #3 fault E47: Replaced clogged liquid-line filter drier')]
# one memory: the background extractor and the `/remember` order both found the fix; the repeat was merged into it
```

`/remember` is an explicit order: the judge model (your `llm` unless you pass a separate `judge`) reads the messages above it and records the case. Without
the order, the background extractor still keeps durable machine knowledge, but more strictly. The evidence stores
Marco's own words, so a reviewer sees exactly what the fix is based on.

## 2. A supervisor reviews

```python
dana = m.scope(workspace_id="acme-hvac", user_id="sup-dana", run_id="review-1", roles=["workspace_admin"])
for row in dana.queue():
    print(row["type"], row["content"], row["evidence"])
    dana.approve(row["id"], note="confirmed on two units")
```

Or from a terminal: `memhub queue --ws acme-hvac`, then `memhub approve <id> --note "..."`. A fix that turns out wrong
is rejected with a note, and a later contradicting report opens a conflict instead of silently replacing the fix.

## 3. Another technician gets the fix

```python
lena = m.scope(workspace_id="acme-hvac", user_id="tech-lena", run_id="job-2210")
lena.search("Carrier rooftop unit low pressure lockout E47", top_k=3)
# [{'type': 'incident', 'status': 'active',
#   'content': 'Carrier 50XC rooftop unit #3 fault E47: Replaced clogged liquid-line filter drier', ...}]
```

In an agent, give each technician's session `m.middleware(...)` or `m.tools(...)`
([Add memory to your own agent](03-add-memory-to-your-agent.md)) so the assistant searches before it diagnoses.

## Adapting it

- **Your own fields.** Add a `types:` block, for example a `machine` type with model, serial and location, or a
  Python class like [examples/maintenance/maint_types.py](../../examples/maintenance/maint_types.py).
- **Per-technician notes.** Add the `user` scope next to `workspace` for a technician's own habits and preferences.
  Those are active at once and never shared.
- **Audit.** `memhub history <memory_id>` shows who proposed, who approved, when, and on which evidence.
