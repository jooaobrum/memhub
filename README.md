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
docker compose up -d                                                        # Postgres + pgvector on port 5433
export MEMHUB_DATABASE_URL=postgresql://memhub:memhub@localhost:5433/memhub
cp memhub.example.yaml memhub.yaml                                          # edit sources, types and models
echo "OPENAI_API_KEY=..." > .env                                            # git-ignored; keys are read from here
memhub init                                                                 # create the tables
memhub ingest --source jsonl                                                # extract memories from your traces
memhub list && memhub queue                                                 # browse, then review what waits for approval
```

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
| Setup | `init`, `reembed` |
| Ingest | `ingest`, `add`, `runs` |
| Browse | `list`, `search`, `history`, `page` |
| Review | `queue`, `approve`, `reject`, `edit`, `promote` |
| Lifecycle | `archive`, `delete` |

Run `memhub <command> --help` for options.

## Configuration

Everything lives in `memhub.yaml` (start from [memhub.example.yaml](memhub.example.yaml)).

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
- [TDD](docs/tdd.md): rules and field-level detail
- [.specs/features/memhub/](.specs/features/memhub/): spec, design and research; [tickets/](.specs/features/memhub/tickets): work items
