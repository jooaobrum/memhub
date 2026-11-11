<p align="center"><img src="docs/img/logo.png" alt="memhub" width="100%"></p>

<p align="center"><b>Reviewed, typed, versioned long-term memory for agents.</b></p>

Agents forget between conversations, and memory tools that write on their own can store things that are not true. memhub keeps a small, trustworthy memory: every item is backed by a real quote, dated, versioned, and approved by a person when it is shared.

It is a library plus CLI (no daemon, no HTTP API) on top of a single Postgres + pgvector database. An ingest pipeline turns conversation traces (JSONL or MLflow) into evidence-backed memories.

## Why

Most agent memory is write-and-hope: the agent saves whatever looks important, and everything saved is used. Over time memories from different users, projects and contexts get mixed. Guesses sit next to facts, old values sit next to new ones, and the agent keeps using things nobody wanted it to use. Nobody can see or fix what it relies on.

That is fine for a toy assistant. It is not fine for systems where the data must be trusted: support, operations, maintenance, anything where a wrong "remembered" fact becomes a wrong answer.

memhub treats memory as data that is **validated before use**:

- A memory is only usable when it is `active`. Candidates, rejected, archived and superseded rows never reach the agent.
- Shared knowledge and contradictions wait in a review queue until a person approves, edits or rejects them.
- Every memory can be inspected, corrected, archived or erased, and every change is a new version with who and when.

The goal is to run this from a simple management UI where a person decides what the agent may remember (see [Manage memories in a UI](#manage-memories-in-a-ui)).

![memhub overview](docs/img/memhub-overview.png)

## Guarantees

| Guarantee | How |
|---|---|
| **Evidence first** | Each memory carries the user's own words; a mechanical check rejects anything without one |
| **Time-aware** | Memories are dated by when they were said, can expire, and newer facts replace older ones with history kept |
| **Human in the loop** | Shared knowledge and contradictions wait for approval; a person's edit is never overwritten by the AI |
| **Small on purpose** | Caps, one value per attribute and strict filters keep memory short and useful |

Works with any agent framework and any LLM provider.

## Manage memories in a UI

memhub ships no UI; building one is out of scope for this repo. It is designed to sit under one, though: every action below already exists in `MemoryService` and the CLI, so a UI is a thin layer on top. The screens are a **concept mockup** (source: [docs/img/review-ui.html](docs/img/review-ui.html)).

**See and validate what the agent knows about a user.** Every memory is shown with the user's own words and whether a person has reviewed it. A reviewer confirms, edits or archives each one, resolves conflicts, and can test a question to see exactly which memories the agent would get.

![A user's memories](docs/img/review-ui-memories.png)

**Correct a memory, keep the history.** Every change is a new version with its source. A person's edit is never overwritten by the AI; a later change only comes back as a proposal to review.

![History and edit](docs/img/review-ui-history.png)

| In the UI | CLI | `MemoryService` |
|---|---|---|
| Review waiting proposals and conflicts | `memhub queue`, `memhub approve <id> [--resolve keep_old\|replace\|keep_both]`, `memhub reject <id>` | `queue()`, `approve()`, `reject()` |
| A user's memories | `memhub list --user <user>` | `list()` |
| Confirm | `memhub edit <memory_id> -f empty.json` (a file containing `{}`) | `edit(fields={})` |
| Edit | `memhub edit <memory_id> -f fields.json` | `edit()` |
| History | `memhub history <memory_id>` | `history()` |
| Archive | `memhub archive <memory_id>` | `archive()` |
| Test what the agent sees | `memhub search "<question>" --user <user>` | `search()` |
| Erase a user | `memhub delete --user <user>` | `delete_user()` |

## Install

Requires Python 3.11+ and Docker (for Postgres with pgvector).

```bash
git clone https://github.com/jooaobrum/memhub.git
cd memhub
uv venv && uv pip install -e ".[openai]"     # or: python -m venv .venv && .venv/bin/pip install -e ".[openai]"
```

Extras pick your LLM provider and sources; combine them as needed (`".[openai,mlflow]"`):

| Extra | Adds |
|---|---|
| `openai`, `anthropic`, `google`, `bedrock`, `ollama`, `mistral` | the LangChain integration for that provider |
| `mlflow` | MLflow traces as an ingest source |
| `dev` | pytest, for running the tests |

## Quickstart

```bash
docker compose up -d                                           # Postgres + pgvector on port 5433
export PGPASSWORD=memhub OPENAI_API_KEY=...                    # or put them in a git-ignored .env
memhub init --preset support --project support \
  --db 'postgresql://memhub:${PGPASSWORD}@localhost:5433/memhub' \
  --embeddings openai:text-embedding-3-small                   # writes memhub.yaml, checks the model, creates the tables
echo '{"content": "Compressor C3 trips when the inlet filter is clogged"}' > fact.json
memhub add --type fact --scope workspace --ws acme -f fact.json
memhub search "inlet filter" --ws acme                         # no -c: memhub.yaml is found by itself
memhub doctor                                                  # db, models, embedding dims, tables
```

The same `memhub.yaml` configures your code:

```python
from memhub import Memory

m = Memory()                                                   # memhub.yaml, MEMHUB_* env, or your own connect: function
me = m.scope(workspace_id="acme", user_id="anna", run_id="run-1")
me.search("inlet filter")
```

Push a conversation straight in, with no log to write and no job to schedule. `add(messages, infer=True)` runs the
same pipeline as `memhub ingest` on the messages you pass and returns an `AddResult` with every memory written
(`ADD`, `PENDING`, `UPDATE`, `MERGE`, `CONFLICT`) and every candidate dropped, with its reason:

```python
out = me.add([{"role": "user", "content": "The C3 compressor trips when the inlet filter clogs"},
              {"role": "assistant", "content": "Noted: check the filter first."}], infer=True)
[(e.event, e.status, e.content) for e in out.results]   # [("PENDING", "candidate", "...")] - workspace memories wait for review
out.dropped                                             # [Dropped(content=..., reason="low_score"), ...]
```

`infer` defaults to `False`: `add("text")` or `add({...fields}, type=...)` stores one memory with no model call. The
same messages sent again are not extracted twice; only the ones after the thread's last push are (`thread_id`
defaults to `run_id`).

`memhub.yaml` is only memhub's own behaviour, a few lines. It is looked up in `./memhub.yaml`, `$MEMHUB_CONFIG`, then `~/.config/memhub/memhub.yaml`; `MEMHUB_*` environment variables (`MEMHUB_PRESET`, `MEMHUB_DB`, nested keys with `__`) and `Memory(config=, overrides=)` override it. `memhub config show` prints every setting with where it came from, `memhub config set KEY VALUE` edits it, and `memhub use acme --user anna` sets the workspace and user for the next commands. Presets: `maintenance`, `support`, `assistant`; a `types:` block replaces or adds to a preset's types. To reuse your app's own pool and models, point `connect: "your_app.memory:build"` at a function returning `{"db": ..., "embedder": ..., "llm": ...}`.

Run the tests with `pip install -e ".[dev]"` and `pytest` (needs Docker; the pgvector container is started by the fixtures).

## Use it in an existing project

Install memhub as a dependency instead of copying files:

```bash
uv pip install "memhub[openai] @ git+https://github.com/jooaobrum/memhub.git"    # or pip install, same spec
```

To pin it in `pyproject.toml`, use `memhub[openai] @ git+https://github.com/jooaobrum/memhub.git@main`. If you use the LangChain middleware, also pin `langchain>=1.0` (memhub declares `>=0.3`, the middleware needs 1.x).

Then add to your repo:

```
<your repo>/
  memhub.yaml                 # models, types, source mapping (start from memhub.example.yaml)
  <app>/memory/service.py     # builds MemoryService once; the only file that imports memhub
  scripts/ingest_memory.py    # optional: entrypoint for a scheduled ingest job
```

```bash
docker compose up -d                                        # or point database_url at your own Postgres with pgvector
memhub init -c memhub.yaml
memhub ingest --source jsonl --dry-run -c memhub.yaml       # check the extraction before writing anything
```

Connect your agent in steps: **A** offline extraction only, **B** your agent calls `service.search(...)`, **C** `MemoryMiddleware` on a LangChain agent (see [chatbot.py](examples/habitantes/chatbot.py)). Start at A. Details, including a separate schema in an existing Postgres and multi-agent setups, are in [docs/integration.md](docs/integration.md).

## CLI

| Group | Commands |
|---|---|
| Setup | `init`, `config show`, `config set`, `doctor`, `use`, `reembed` |
| Ingest | `ingest`, `add`, `runs` |
| Browse | `list`, `search`, `history`, `page` |
| Review | `queue`, `approve`, `reject`, `edit`, `promote` |
| Lifecycle | `archive`, `delete` |

Run `memhub <command> --help` for options.

## Configuration

Everything lives in `memhub.yaml`: `memhub init` writes a small one, or start from [memhub.example.yaml](memhub.example.yaml). Every key of the full examples stays valid.

- **Your database:** `database_schema`, `sources.<x>.kind: sql`
- **Your tenants:** `fields.workspace_id`
- **Your schemas:** declare types in the yaml, or a class next to it
- **Any model:** `base_url`, Azure `params`, `factory`
- **Any agent stack:** `memhub.toolkit.MemoryToolkit`

Ingest quality knobs:

- `ingestion.segment_max_user_turns`: long threads are extracted in slices
- `extraction.retries`: cheap models truncate JSON
- `ingestion.verify_episodes`, `verify_claims`, `repair_relative_time`: judge-model checks
- `reconcile.compare_floor`, `compare_max`, `compare_across`: updates and contradictions are found among the nearest rows, not only above 0.80 similarity

## Examples

### Example projects

| Example | Shows |
|---|---|
| [examples/habitantes](examples/habitantes) | A chatbot with memhub memory: config, synthetic sample data, LangChain agent, functional check |
| [examples/support](examples/support) | Adapting memhub to a support project: config only |
| [examples/maintenance](examples/maintenance) | Adapting memhub to maintenance work, with a project memory type in Python ([maint_types.py](examples/maintenance/maint_types.py)) |

### Review workflows

The commands below use the Habitantes config (`C=examples/habitantes/memhub.yaml`, after `memhub ingest --source jsonl -c $C`). Every command prints JSON; `<id>` is a row id from `queue`, and `<memory_id>` is the stable id shared by all versions of a memory.

**Resolve a contradiction.** The user first asked for short answers and later for step-by-step detail. Both values are kept until someone decides:

```bash
memhub queue -c $C                                   # conflicts first, each with its quote and the row it conflicts with
memhub approve <id> --resolve replace -c $C          # the new value becomes active; the old one stays in history
# or: --resolve keep_old, or --resolve keep_both (one row holding both values)
```

**Approve shared knowledge.** A glossary term or a promoted episode is visible to every user, so it waits for an admin:

```bash
memhub approve <id> --note "checked with the official website" -c $C
memhub reject <id> --note "personal detail, not general" -c $C
```

**Correct what the AI extracted.** Only the fields you pass change. The new version is marked verified, and later ingests never overwrite it:

```bash
cat > fix.json <<'EOF'
{"content": "Lives in a shared flat near campus since May 2026; receives a housing allowance."}
EOF
memhub edit <memory_id> -f fix.json -c $C
memhub history <memory_id> -c $C                     # every version with its evidence, author and date
```

**Clean up.** Find what has expired, hide what should not be used, erase a user on request:

```bash
memhub list --user demo-ana --stale -c $C            # memories past their valid_until (never served to the agent)
memhub archive <memory_id> -c $C                     # hidden from the agent, history kept
memhub delete --user demo-ana -c $C                  # permanent erasure of every row for this user
```

**Audit what was dropped.** Every candidate the pipeline rejected is logged with a reason (see [last_run.json](examples/habitantes/last_run.json)):

```bash
memhub runs -c $C                                    # proposed / created / merged / conflicts, and dropped_by_reason
```

Approving and rejecting need a role from `roles.approve_workspace` (default `workspace_admin`). The CLI acts as `cli:<your OS user>` with the roles in `MEMHUB_CLI_ROLES`.

## Documentation

- [Architecture](docs/architecture.md): diagrams and system overview
- [Integration](docs/integration.md): adoption steps
- [SQLite](docs/sqlite.md): one local file, no Docker; `memhub[sqlite]` for vector search in SQL
- [TDD](docs/tdd.md): rules and field-level detail
- [.specs/features/memhub/](.specs/features/memhub/): spec, design and research; [tickets/](.specs/features/memhub/tickets): work items
