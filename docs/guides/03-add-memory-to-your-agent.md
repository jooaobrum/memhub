# Add memory to your own agent

This guide adds per-user memory to an application entirely from code: no CLI, no yaml. The example is a travel
assistant, with three integration styles from least to most automatic:

1. Call `search` and `add` yourself.
2. Give the model memory tools (any framework).
3. Attach `MemoryMiddleware` to a LangChain agent, which injects memory into every turn.

```bash
uv pip install "memhub[openai] @ git+https://github.com/jooaobrum/memhub.git" "langchain>=1.0"
```

## Configure in code

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from memhub import Memory

m = Memory(
    db="sqlite:///travel.db",                      # or a postgresql:// URL, or your own pool / sqlite3 connection
    llm=ChatOpenAI(model="gpt-4.1-mini"),          # any LangChain chat model
    embedder=OpenAIEmbeddings(model="text-embedding-3-small"),
    config={"project": "travel", "memory": "./memory.md"},   # what this assistant remembers
)
m.init()                                           # creates the tables; safe to call on every start
```

`memory.md` says what the assistant remembers: its kinds of memory, and the rules for what to keep in plain words.
Start from the one `memhub init` writes (run it once in a scratch folder and copy the file); it keeps facts,
preferences and who the user is, with rules tuned on real conversations. [docs/memory.md](../memory.md) describes
the file. With no memory file at all, memhub keeps the same three kinds of memory, but with short built-in rules. You
can also pass the keys inline:

```python
config={"project": "travel",
        "types": {"profile": "builtin", "fact": "builtin", "trip": "Travels to {city}[ every {frequency?}]"},
        "extraction": {"instructions": "Keep where the user lives, what they eat, and their regular trips."}}
```

Every call needs a workspace, a user and a run. Bind them once per request:

```python
me = m.scope(workspace_id="travel-app", user_id="u-42", run_id="session-1")
```

## 1. Search and add yourself

```python
me.add("Prefers window seats on flights")          # one memory, no model call

out = me.add([
    {"role": "user", "content": "I'm vegetarian and I live in Porto. I travel to Berlin for work every month."},
    {"role": "assistant", "content": "Got it, I'll keep that in mind for restaurant tips."},
], infer=True)                                     # the model extracts what is worth keeping
print([(e.event, e.content) for e in out.results])
# [('ADD', 'Is vegetarian and lives in Porto.'), ('ADD', 'Travels to Berlin for work every month.')]

for r in me.search("where does the user live and what do they eat", top_k=5):
    print(r["type"], r["content"])
# profile Is vegetarian and lives in Porto.
# fact    Travels to Berlin for work every month.
# fact    Prefers window seats on flights
```

Put the results into your prompt. Call `add(..., infer=True)` after each exchange, or at the end of a conversation.
Messages already pushed for the same thread (`thread_id`, default `run_id`) are not extracted twice.

## 2. Memory tools for any framework

```python
kit = m.tools(workspace_id="travel-app", user_id="u-42", run_id="req-1")
kit.tool_specs()                                   # OpenAI-style function specs: search_memory, propose_memory
kit.call("search_memory", {"query": "diet"})       # a JSON string: what your tool loop returns to the model
```

`propose_memory` stores a candidate, so the model can suggest a memory but cannot activate one on its own.

## 3. LangChain middleware: memory in every turn

One middleware serves every user of the app. It reads the user from each invocation's context:

```python
from dataclasses import dataclass
from langchain.agents import create_agent

@dataclass
class Ctx:
    user_id: str

agent = create_agent(
    ChatOpenAI(model="gpt-4.1-mini"), tools=[],
    middleware=[m.middleware(workspace_id="travel-app", user_from=lambda ctx: ctx.user_id)],
    context_schema=Ctx,
)
out = agent.invoke({"messages": [{"role": "user", "content": "Suggest a dinner spot for my next work trip. One line."}]},
                   context=Ctx(user_id="u-42"))
print(out["messages"][-1].content)
# For your next work trip in Berlin, try "Kopps" – a top-notch vegetarian restaurant ...
```

The user never said "Berlin" or "vegetarian" in this conversation. The middleware put the user's memories into the
prompt, and the agent also gets `search_memory` and `propose_memory` as tools.

## In production

- **One module owns memhub.** Build `Memory` once (for example in `app/memory.py`) and import it everywhere.
  Removing or swapping memhub then touches one file.
- **Postgres.** Pass `db="postgresql://…"` or your own `psycopg_pool.ConnectionPool`. `database_schema` keeps memhub
  in its own schema of your existing database.
- **Same config for code and ops.** `Memory()` with no arguments reads `memhub.yaml` and `MEMHUB_*` variables, so your
  app and the `memhub` CLI share one config.
- **Erasure.** `memhub delete --user u-42` removes every row of that user.

Reference: [docs/integration.md](../integration.md).
