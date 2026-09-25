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

## Quickstart

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv venv && uv pip install -e ".[dev,openai,mlflow]"
docker compose up -d
export MEMHUB_DATABASE_URL=postgresql://memhub:memhub@localhost:5433/memhub
cp memhub.example.yaml memhub.yaml
memhub init
memhub ingest --source jsonl
```

Provider extras: `openai`, `anthropic`, `google`, `bedrock`, `ollama`, `mistral`, plus `mlflow` for MLflow traces. API keys are read from a git-ignored `.env` (e.g. `OPENROUTER_API_KEY`, `OPENAI_API_KEY`).

Run the tests with `pytest` (needs Docker; the pgvector container is started by the fixtures).

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
- Pilot (Habitantes): `memhub.yaml` points at `data/*.jsonl`. After `init` + `ingest`, run `python pilot/functional_check.py` for an end-to-end PASS/FAIL of every command.

## Documentation

- [Architecture](docs/architecture.md): diagrams and system overview
- [Integration](docs/integration.md): adoption steps
- [TDD](docs/tdd.md): rules and field-level detail
- [.specs/features/memhub/](.specs/features/memhub/): spec, design and research; [tickets/](.specs/features/memhub/tickets): work items

## Known v1 limitations

- `cost_usd` is not computed (tokens are)
- `rephrase` signals are not detected
- The correction detector's LLM fallback is off unless `ingestion.llm_correction_check: true`
- Middleware entity boost is not wired
