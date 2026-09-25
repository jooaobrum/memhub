# memhub

Reviewed, typed, versioned long-term memory for agents.

Agents forget between conversations, and memory tools that write on their own can store things that are not true. memhub keeps a small, trustworthy memory: every item is backed by a real quote, dated, versioned, and approved by a person when it is shared.

It is a library plus CLI (no daemon, no HTTP API) on top of a single Postgres + pgvector database. An ingest pipeline turns conversation traces (JSONL or MLflow) into evidence-backed memories.

![memhub overview](docs/img/memhub-overview.png)

## Guarantees

| Guarantee | How |
|---|---|
| **Evidence first** | Each memory carries the user's own words; a mechanical check rejects anything without one |
| **Time-aware** | Memories are dated by when they were said, can expire, and newer facts replace older ones with history kept |
| **Human in the loop** | Shared knowledge and contradictions wait for approval; a person's edit is never overwritten by the AI |
| **Small on purpose** | Caps, one value per attribute and strict filters keep memory short and useful |

Works with any agent framework and any LLM provider.

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

As a dependency in another project: `pip install "memhub[openai] @ git+https://github.com/jooaobrum/memhub.git"`.

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

- [examples/support](examples/support) and [examples/maintenance](examples/maintenance): adapting memhub to another project
- [examples/habitantes](examples/habitantes): a chatbot with memhub memory (config, sample data, LangChain agent, functional check)

## Documentation

- [Architecture](docs/architecture.md): diagrams and system overview
- [Integration](docs/integration.md): adoption steps
- [TDD](docs/tdd.md): rules and field-level detail
- [.specs/features/memhub/](.specs/features/memhub/): spec, design and research; [tickets/](.specs/features/memhub/tickets): work items
