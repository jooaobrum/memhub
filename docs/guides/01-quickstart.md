# Quickstart: memory for your agent in five minutes

memhub gives an agent long-term memory that a person can trust. It stores each memory with the user's own words as
evidence and a date. Every change becomes a new version, and anything shared waits for a person's approval. This guide
goes from an empty folder to a working memory with the CLI and with Python.

You need Python 3.11+ and an OpenAI API key. SQLite is used here, so no database server is required.

## 1. Install

memhub is installed from git. **Do not run `pip install memhub`**: that name on PyPI belongs to an unrelated project.

```bash
mkdir my-agent && cd my-agent
uv venv && source .venv/bin/activate
uv pip install "memhub[openai] @ git+https://github.com/jooaobrum/memhub.git"
echo "OPENAI_API_KEY=sk-..." > .env       # memhub reads .env from this folder (keep it out of git)
```

Extras pick your model provider and sources: `openai`, `anthropic`, `google`, `bedrock`, `ollama`, `mistral`,
`mlflow`, `mcp` (Claude Code), `sqlite` (vector search inside SQLite). Combine them: `"memhub[openai,mcp]"`.

## 2. Initialize

```bash
memhub init --db sqlite:///$HOME/.memhub/my-agent.db \
  --embeddings openai:text-embedding-3-small \
  --chat openai:gpt-4.1-mini
```

```
initialized my_agent_memory / my_agent_memory_runs
wrote /…/my-agent/memhub.yaml
wrote /…/my-agent/memory.md
```

`init` checks that the embedding model answers and creates the tables. Only then does it write two files:

- `memhub.yaml`: the database and the models;
- `memory.md`: what the memory keeps.

It asks nothing when you pass flags, so it runs the same way in CI or from a coding agent, and the project takes this
folder's name. Run `init` with no flags to have it ask instead; it then also asks what the memory should keep. Run
`memhub doctor` at any time to check the database, the models and the tables.

> Keep SQLite files out of Google Drive, Dropbox or iCloud folders: the sync client can lock the file mid-write.

## 3. Say what to remember

Open `memory.md`. The starter keeps three kinds of memory: facts, preferences and who the user is. Its body says what to
keep in plain words: one line on what this memory is for, then general rules tuned on real conversations (write in the
third person, never keep questions or errands, date everything, how preferences work):

```markdown
---
types:
  fact: builtin
  preference: builtin
  profile: builtin
---
# What to remember

Durable facts about the user and their work, what they prefer, and who they are.

## Kinds
...
## Voice
...
## Keep / ## Never / ## Time / ## Preferences / ## Examples
...
```

Rewrite the first line for your agent, and adapt the rules: write them the way you would brief a new colleague, with
what is worth remembering about a user and what must never be kept. To add your own kinds of memory, add a line under `types:`, where the placeholders
are the fields:

```yaml
  trip: "Travels to {city}[ every {frequency?}]"
```

[docs/memory.md](../memory.md) describes every form, and [docs/memory-setup.md](../memory-setup.md) is a prompt you
can give any assistant to write this file with you.

## 4. Add and search from the CLI

Every memory belongs to a **workspace** (a tenant, a project, a team) and optionally a **user**.

```bash
echo '{"content": "Prefers answers in short bullet points", "key": "format"}' > pref.json   # a preference is one row per key
memhub add --type preference --scope user --ws acme --user anna -f pref.json
memhub search "how should I format answers" --ws acme --user anna
```

To set the workspace and user once for the next commands:

```bash
memhub use acme --user anna
memhub list            # every memory, with its status, evidence and version
```

## 5. The same memory from Python

```python
from dotenv import load_dotenv; load_dotenv()
from memhub import Memory

m = Memory()                                    # reads ./memhub.yaml
me = m.scope(workspace_id="acme", user_id="anna", run_id="session-1")

me.add("Lives in Porto")                        # stored as is: no model call
out = me.add([                                  # extracted by the model from a conversation
    {"role": "user", "content": "I'm vegetarian and I travel to Berlin for work every month."},
    {"role": "assistant", "content": "Noted, I'll keep that in mind for restaurant tips."},
], infer=True)

for e in out.results:
    print(e.event, e.type, e.content)
# e.g.  ADD profile Is vegetarian.
#       ADD fact    Travels to Berlin for work every month.   (model output: wording varies between runs)

for r in me.search("what does Anna eat?", top_k=3):
    print(r["type"], r["content"])
```

`add(messages, infer=True)` returns every memory written (`ADD`, `UPDATE`, `MERGE`, `PENDING`, `CONFLICT`) and every
candidate it dropped, with the reason in `out.dropped`. If the same messages are sent again they are not extracted a
second time.

## 6. What happens to a memory

| A memory that is… | Is |
|---|---|
| about one user, extracted or added | `active` at once and served to the agent |
| shared with the workspace (a glossary term, a fix, an account fact) | a `candidate` until someone runs `memhub approve <id>` |
| contradicting an active one | a conflict in `memhub queue`, resolved with `--resolve keep_old\|replace\|keep_both` |
| edited by a person | a new version marked `verified`; a later extraction never overwrites it |

`memhub history <memory_id>` shows every version with who wrote it and when.

## Next

- [Give Claude Code a memory](02-claude-code.md)
- [Add memory to your own agent](03-add-memory-to-your-agent.md)
- [Learn from your chat logs](04-learn-from-chat-logs.md)
- [Field-service agents that share fixes](05-field-service-agents.md)
- [Tune memhub to your own domain](06-your-own-domain.md)
