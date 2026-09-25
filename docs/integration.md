# memhub — Integration Guide

How to adopt memhub in a project. For behaviour see [tdd.md](tdd.md); for structure see [architecture.md](architecture.md).

## 1. Pick an integration level

| Level | What you get | You provide | Effort |
|---|---|---|---|
| **A. Offline extraction + CLI** | Reviewed memories from your logs; inspect with `list/search/page` | Postgres, `memhub.yaml`, logs (JSONL or MLflow) | ~1 hour |
| **B. Read API in your agent** | Memories in your prompts, any framework | A: plus `MemoryService` calls | ~½ day |
| **C. LangChain middleware** | Auto-injection per turn, tools, trace ids | B: plus `create_agent(middleware=[...])` | ~1 hour on top of B |
| **D. Custom source or types** | Non-standard trace formats, domain memory types | Adapter class / type classes | Varies |

Start at A: it needs no change to the agent and shows what would be extracted before anything reaches a prompt.

## 2. Prerequisites

- Python ≥ 3.11.
- Postgres with the `pgvector` extension (tested on `pgvector/pgvector:pg16`). Local: `docker compose up -d` (image `pgvector/pgvector:pg16`, port 5433, user/password/db `memhub`).
- A chat model **with structured output** for `extractor` and `judge` (they may be different models), and an embedding model. Anthropic has no embedding model; pair it with OpenAI, Voyage or a local model.
- The role that runs `memhub init` must be allowed `CREATE EXTENSION vector` and create tables.

### 2.1 Install memhub as a package

Install memhub as a dependency; do not copy its source into your project (you would fork it and lose upgrades and its tests). It is not on PyPI, so point at it:

| Option | How | Use when |
|---|---|---|
| Git dependency | `memhub @ git+ssh://git@<host>/<org>/memhub.git@v0.1.0` in your `pyproject.toml` (with uv: `[tool.uv.sources]`) | The repo has a remote. Pin a tag or commit |
| Private index or wheel | `uv build` in the memhub repo (hatchling is configured), publish the wheel to your index or artifact store, then `pip install memhub==0.1.0` | Several projects or CI consume it |
| Editable path | `uv pip install -e ../memhub` | Local development on both projects at once |

Extras: `memhub[openai]`, `memhub[anthropic]`, `memhub[mlflow]`; install the ones matching your models and sources. The install also provides the `memhub` CLI.

Tag releases and pin them. memhub has no migration tool, so you decide when to upgrade and re-run `memhub init` (idempotent).

### 2.2 What lives in your project

Everything except the adapter stays inside memhub. Your project owns only:

```
<repo>/
  memhub.yaml                  # models, types, areas, source mapping, extraction instructions
  <app package>/memory/
    service.py                 # builds MemoryService once (the only place that imports memhub)
    logging.py                 # writes turn logs, only if you have none
    types.py                   # custom memory types, only if you add any
    sources.py                 # custom source adapter, only if you add one
  scripts/ingest_memory.py     # optional entrypoint for the scheduled ingest job
```

Two rules keep the coupling low:

- **One adapter module.** Only `memory/service.py` imports memhub; agents call it (`get_memory()`), or receive `MemoryMiddleware` from it. Swapping or removing memhub then touches one file.
- **Two independent processes.** The agent process reads (levels B/C). The ingest job reads logs and writes memories, and runs on a schedule. They share only the database and `memhub.yaml`.

Multi-agent projects (a main agent plus specialist agents):

- Agents with their own model loop: give each the middleware. They share a user's memory automatically, since memories are keyed by `(workspace_id, user_id)`.
- Sub-agents invoked as tools: don't attach the middleware. Pass the relevant memory text in their input, or call `service.search(...)` from the tool.
- Agents never write memories directly. `propose_memory` creates candidates only.
- Separate memory per agent needs a distinct `workspace_id` per agent on the read side. Ingest tags all interactions with `workspace_default`, so separate workspaces also need separate sources and runs. Start with one shared workspace.

Install and configure first (2.1, `memhub init`), then log turns and ingest with `--dry-run` (level A) before attaching anything to an agent.

Middleware (level C) needs LangChain 1.x (`langchain.agents.middleware`, `ToolRuntime`). `pyproject.toml` still declares `langchain>=0.3`; pin `langchain>=1.0` in your project.

### 2.3 Azure OpenAI (API key, token, or Entra ID)

Untested against a live Azure resource: the `params` names come from the LangChain Azure classes, so run one small `--dry-run` first.

**Config.** Endpoint, deployment and API version go in `params`. `api_key_env` is required by the schema but only used if that variable is set, so name any variable when you authenticate with a token. `model` is still required; `azure_deployment` selects the deployment.

```yaml
llm:
  extractor: {provider: azure_openai, model: gpt-4.1-mini, api_key_env: AZURE_OPENAI_API_KEY,
              params: {azure_endpoint: "https://<resource>.openai.azure.com", azure_deployment: <chat-deployment>, api_version: "2024-10-21"}}
  judge:     {provider: azure_openai, model: gpt-4.1-mini, api_key_env: AZURE_OPENAI_API_KEY,
              params: {azure_endpoint: "https://<resource>.openai.azure.com", azure_deployment: <chat-deployment>, api_version: "2024-10-21"}}
embeddings:
  provider: azure_openai
  model: text-embedding-3-small
  api_key_env: AZURE_OPENAI_API_KEY
  dims: 1536                         # must match the deployed model; fixed at `memhub init`
  params: {azure_endpoint: "https://<resource>.openai.azure.com", azure_deployment: <embedding-deployment>, openai_api_version: "2024-10-21"}
```

The chat deployments must support structured output.

**Which auth path to use**

| Auth | Works with stock `memhub` CLI | Works in a long-running agent | How |
|---|---|---|---|
| API key | yes | yes | `export AZURE_OPENAI_API_KEY=...` |
| Entra token in an environment variable | yes | no (token expires in about 1h) | `export AZURE_OPENAI_AD_TOKEN=$(az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv)` |
| Entra ID / managed identity, auto-refresh | via a wrapper (below) | yes, in code | `azure_ad_token_provider` |

With a token in the environment, leave the API-key variable unset; LangChain reads `AZURE_OPENAI_AD_TOKEN`. Use this for the CLI, one-off runs and short batch jobs.

**CLI with an auto-refreshing token provider.** A callable cannot be written in YAML, and the stock CLI builds its models from YAML. Wrap it: the CLI looks its model factories up in its own module at call time, so you can replace them and then run the same `app`. Put this in your project as `memhub_azure.py`:

```python
"""Run the memhub CLI with Entra ID auth: `python -m memhub_azure ingest --source jsonl`."""
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

import memhub.cli as cli

_token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")

def _chat(cfg):
    p = cfg.params
    return AzureChatOpenAI(azure_endpoint=p["azure_endpoint"], azure_deployment=p["azure_deployment"],
                           api_version=p["api_version"], azure_ad_token_provider=_token)

def _embeddings(cfg):
    p = cfg.params
    return AzureOpenAIEmbeddings(azure_endpoint=p["azure_endpoint"], azure_deployment=p["azure_deployment"],
                                 openai_api_version=p["openai_api_version"], azure_ad_token_provider=_token)

cli.build_chat_model = _chat          # used by `ingest` (extractor and judge)
cli.build_embeddings = _embeddings    # used by every command that embeds (search, add, edit, reembed, ingest)

if __name__ == "__main__":
    cli.app()
```

Then use it instead of `memhub`, with the same commands and options:

```bash
python -m memhub_azure init   -c memhub.yaml
python -m memhub_azure ingest -c memhub.yaml --source jsonl --dry-run
python -m memhub_azure search -c memhub.yaml "query" --user u42
python -m memhub_azure queue  -c memhub.yaml
```

`DefaultAzureCredential` picks up, in order, environment credentials, workload or managed identity, and your `az login` session, so the same file works on a laptop, in CI and in a pod. Requires `azure-identity` and `langchain-openai`.

**Agent and service code** (levels B and C) build the objects directly and pass them in; nothing to patch:

```python
service = MemoryService(store=..., settings=settings, registry=settings.build_registry(),
                        embeddings=AzureOpenAIEmbeddings(..., azure_ad_token_provider=_token))
```

For a custom ingest entrypoint, pass `extractor=AzureChatOpenAI(...)`, `judge=...` and the embeddings to `ingest_source()` (see 7.1).

**Watch for**
- The ingest job, the agent and every CLI call must use the same embedding deployment. A different model or `dims` is refused at startup (`EmbeddingMismatch`).
- Azure content filters can reject some prompts. The API error is raised, not recorded as `extract_error` (that is only for unparseable output): the segment is rolled back, counted in `failed_segments`, the thread stops there, and it is retried on the next run. A prompt that is always filtered blocks its thread until you change or skip it. Check `failed_segments` in the ingest summary after the first real run.
- Deployment names are not model names. `azure_deployment` must be the name you gave the deployment in Azure.

## 3. Level A: offline extraction

### 3.1 Write `memhub.yaml`
Copy `memhub.example.yaml` and change these, in order:

1. **Identity:** `project_prefix` (table prefix; unique per project sharing a DB), `database_url: ${MEMHUB_DATABASE_URL}`, `workspace_default`.
2. **Models:** `llm.extractor`, `llm.judge`, `embeddings` (with `dims`; fixed at `init`).
   ```yaml
   llm:
     extractor: {provider: anthropic, model: claude-haiku-4-5-20251001, api_key_env: ANTHROPIC_API_KEY}
     judge:     {provider: anthropic, model: claude-haiku-4-5-20251001, api_key_env: ANTHROPIC_API_KEY}
   embeddings: {provider: openai, model: text-embedding-3-small, api_key_env: OPENAI_API_KEY, dims: 1536}
   ```
3. **Scopes and types:** turn on only what you need. `scopes: [user]` if nothing is shared; add `workspace` only if you use `term` or admin-approved shared memories.
4. **Source mapping** (§3.2).
5. **Policy:** `extraction.instructions` states what is worth remembering in *your* domain. This is the highest-leverage setting. `ingestion.skip_when` skips trivial turns using message metadata.
6. **Areas** (only if you keep `fact`): `areas.seeds` lists your topic buckets (`key, title, description`; the description is what the model and search match against). `fact` with `area: required` drops any fact that fits no area.

Secrets stay in environment variables (`api_key_env`, `${ENV}` in YAML). The CLI loads a `.env` from the working directory.

### 3.2 Map your logs

**JSONL**: one line per turn (a user message and its answer), or you can normalise beforehand.
```yaml
sources:
  jsonl:
    kind: jsonl
    path: logs/interactions.jsonl        # relative to the directory holding memhub.yaml
    fields:
      thread_id: chat_id                 # conversation id; segmentation and watermarks key on it
      user_id: chat_id                   # who owns the memories (defaults to thread_id)
      message_id: message_id             # must be unique and stable
      timestamp: timestamp               # ISO-8601; naive = UTC
      trace_id: trace_id
      user_content: user_query
      assistant_content: answer
      metadata: [intent, category]       # copied to messages; usable in skip_when
    signals:                             # optional feedback file
      path: logs/feedback.jsonl
      join_on: {message_id: message_id, thread_id: chat_id}
      map: {rating: {down: feedback_down, up: feedback_up}}
```
Assistant messages get id `<message_id>:a`. Lines with a missing id or unparseable timestamp are skipped and counted in the run summary (`skipped_lines`).

**MLflow**: one trace = one turn.
```yaml
sources:
  mlflow:
    kind: mlflow
    tracking_uri: http://mlflow:5000
    experiment: my-agent
    fields:
      thread_id: mlflow.trace.session    # trace tag/metadata key (default shown)
      user_id: mlflow.trace.user
      user_content: messages.-1.content  # dotted path into the request; omit if the request is a string
      assistant_content: output          # dotted path into the response
      metadata: [intent]
```
Traces without a thread id or non-empty user/assistant text are skipped. Error-state traces set `metadata.error`. Requires `memhub[mlflow]`.

**Requirements on your data:** stable unique `message_id`; timestamps in true event time (memories are dated from them); one `thread_id` per conversation; a `user_id` that identifies the person across threads.

### 3.3 Run

```bash
export MEMHUB_DATABASE_URL=postgresql://memhub:memhub@localhost:5433/memhub
memhub init   -c memhub.yaml                       # tables, extension, saves embedding model+dims
memhub ingest -c memhub.yaml --source jsonl --dry-run   # full logic, rolled back; costs tokens
memhub ingest -c memhub.yaml --source jsonl        # prints a JSON run summary
memhub runs   -c memhub.yaml                       # dropped-by-reason, tokens
memhub list   -c memhub.yaml --user 12345
memhub page   -c memhub.yaml --user 12345
memhub queue  -c memhub.yaml                       # candidates awaiting review
```

Notes:
- Only threads idle ≥ `segment_idle` (1h) are processed; recent conversations wait for a later run.
- Re-running is safe and free when nothing new arrived. `--reprocess` ignores watermarks (evidence dedup keeps the ledger consistent); `--thread <id>` limits to one thread.
- Schedule `memhub ingest` from cron/Airflow/K8s CronJob. Run one instance at a time.
- **Tune from the drops:** read `memhub runs`. Many `low_score` → lower `admission.threshold` or raise utility guidance; many `ungrounded` → model too weak or truncated; many `no_area` → seeds too narrow.

### 3.4 Review workflow

| Item | Behaviour | Action |
|---|---|---|
| User-scope memory | Active immediately, `verified=false` | `edit` (marks verified), `archive`, `delete` |
| Workspace-scope memory, or any conflict | `candidate`, invisible to search | `queue` → `approve [--resolve keep_old\|replace\|keep_both]` / `reject` |
| Wrong area layout | – | `areas`, `areas merge <from> <to>` |
| Something needs adding by hand | – | `add --type fact --scope user --user <id> --file x.json` (fields validated against the type) |
| Erasure request | – | `delete --user <id>` |

Once a person edits or approves a row it is `verified`, and the extractor will never overwrite it: contradictions arrive as conflict candidates.

CLI identity: `cli:<os user>`; roles from `MEMHUB_CLI_ROLES` (default `workspace_admin`).

## 4. Level B: read memories from any agent

Build a `MemoryService` once. This is what `cli.py` does; there is no factory function to import, so copy it:

```python
from memhub.config import load_config, build_embeddings
from memhub.service import MemoryService, Actor
from memhub.store import MemoryStore

settings = load_config("memhub.yaml")
service = MemoryService(
    store=MemoryStore(settings.database_url, settings.project_prefix),
    settings=settings,
    registry=settings.build_registry(),
    embeddings=build_embeddings(settings.embeddings),   # any object with embed_query(str) -> list[float]
)
agent_actor = Actor(id="agent")                          # no roles: read + propose only
```

Per turn:

```python
rows = service.search(agent_actor, user_message,
                      workspace_id="acme", user_id=user_id, k=5)
# each row: id, memory_id, version, type, content, verified, assertion,
#           observed_at, valid_until, evidence, areas, expanded_by?

page = service.page_for_query(user_message, user_id, workspace_id="acme")
# {title, summary, details[], last_updated} or None

profile = service.list(agent_actor, status="active", type="profile",
                       user_id=user_id, workspace_id="acme")
```

Render into your prompt yourself, or reuse the middleware's line format:
`[<id>|<type>|verified|unverified] <content> (as of YYYY-MM-DD[, inferred]) (<sources>)`. Guidelines:

- Tell the model that `unverified` and `inferred` items are less trustworthy, and never to state an auto-summary as a cited fact.
- Skip rows with `valid_until` in the past. `search` already does; `list` marks them with `stale`.
- Log `f"{row['memory_id']}@{row['version']}"` for everything you inject, so any answer traces back to a version and its quotes. `memhub history <memory_id>` shows the chain.

Other calls: `propose(actor, type=, scope=, fields=, evidence=, ...)` writes a `candidate` only. `add` writes directly (user scope: active and verified). Handle `ServiceError` subclasses: `PermissionDenied`, `ValidationError`, `InjectionDetected`; `NotFound` for missing ids.

Identity: `user_id` **must come from your authenticated session**, never from model output. It decides whose memories are read.

Consistency note: the ingest worker and the agent must use the same embedding model and `dims`; every `MemoryService` call checks against what `init` saved and raises `EmbeddingMismatch` on drift.

`MemoryStore` holds no shared connection (it opens a short-lived one per operation, no pool), so one service instance can be shared. Add a pooler (pgbouncer) for high request rates.

## 5. Level C: LangChain middleware

```python
from langchain.agents import create_agent
from memhub.middleware import MemoryMiddleware
from dataclasses import dataclass
from langgraph.checkpoint.memory import InMemorySaver

@dataclass
class Ctx:
    workspace_id: str
    user_id: str

agent = create_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[...],
    middleware=[MemoryMiddleware(
        service,
        workspace_from=lambda ctx: ctx.workspace_id,   # or a constant string
        user_from=lambda ctx: ctx.user_id,
        k=5,
    )],
    context_schema=Ctx,
    checkpointer=InMemorySaver(),                      # see below
)
agent.invoke({"messages": [("user", "…")]},
             {"configurable": {"thread_id": "t1"}},
             context=Ctx("acme", "u42"))
```

What it does:

- **Once per thread:** `<about_the_user>` (Profile, then Preference, each within its `max_chars`) and a skill index.
- **Every turn:** a `<memory>` block from `search`, and an `<area_page>` when the query is close enough to one of the user's areas (`areas.page_min_similarity`, budget `areas.page_max_chars`).
- Appended to the system message **for that model call only**; the stored message history is unchanged.
- Adds tools `search_memory`, `load_skill`, `propose_memory`. Proposals are always candidates.
- Writes the injected `memory_id@version` list to state key `memhub_injected` and to the active MLflow trace metadata (`memhub_injected`).

Caveats:

- The "fixed per thread" snapshot needs a **checkpointer**; without one it is rebuilt each `invoke`.
- Hooks are synchronous; each turn costs DB queries plus two query embeddings (search and page lookup).
- `workspace_from` / `user_from` receive `runtime.context`; a plain string is used as-is.
- Entity boost is not wired. Only `active` rows are injected, so workspace candidates stay invisible until approved.
- If your tools need to know about injection, set `retrieval` per type in config: `always` (in snapshot), `search` (per-turn), `index_then_load` (skills).

## 6. Other frameworks

memhub has no framework adapters besides LangChain. For LlamaIndex, OpenAI Agents SDK, custom loops, or another language:

- **Python, any framework:** level B. Call `search` before the model call, inject text, optionally expose `service.propose` as a tool.
- **Non-Python service:** call the CLI (`memhub search "<q>" --user <id>` prints JSON) or wrap `MemoryService` in a small HTTP service you own. Extraction (level A) needs no runtime coupling in either case: it reads your logs.
- **Feedback from your app:** write it to a JSONL feedback file joined on `message_id`/`thread_id` (`feedback_down` lowers the admission score, `feedback_up` raises it); or implement `signals()` in a custom source.

## 7. Level D: extensions

### 7.1 Custom source
Implement the adapter and call `ingest_source()` directly. The CLI only builds `jsonl` and `mlflow` sources, so a custom source needs your own entrypoint:

```python
from memhub.sources.base import Interaction
from memhub.pipeline.ingest import ingest_source
from memhub.config import build_chat_model

class PostgresLogSource:
    skipped = 0
    def read(self):
        for r in fetch_rows():
            yield Interaction(
                thread_id=r.conv_id, user_id=r.user_id, workspace_id="acme",
                message_id=r.msg_id, role=r.role,            # "user" | "assistant" | "tool"
                content=r.text, timestamp=r.ts,               # tz-aware datetime
                trace_id=r.trace_id, metadata={"intent": r.intent})
    def signals(self):                                        # optional
        return [{"thread_id": "c1", "message_id": "m1", "kind": "feedback_down", "detail": "rating=down"}]

summary = ingest_source(
    store=service.store, settings=settings, registry=service.registry,
    source=PostgresLogSource(), source_name="pg",
    extractor=build_chat_model(settings.llm.extractor),
    judge=build_chat_model(settings.llm.judge),
    embeddings=service.embeddings,
)
```
`source_name` keys the watermarks and evidence `source`, so keep it stable. Message ids must be unique per thread and stable across runs. Signal kinds recognised by scoring: `correction`, `feedback_up` (raise), `feedback_down`, `rephrase`, `error` (lower).

### 7.2 Custom memory types
```python
# myproj/memory_types.py
from memhub.types import MemoryBase

class Machine(MemoryBase):
    model: str
    location: str | None = None
```
```yaml
entity_types: [machine]
types:
  machine: {class: "myproj.memory_types:Machine", retrieval: search, type_prior: 0.7, max_active: 50}
```
The class must be importable in every process that touches the ledger (ingest worker, agent, CLI). Change a type's shape by bumping `schema_version`; rows keep validating against the class of their own version. Types with `retrieval: index_then_load` need `name`/`description`/`body`, like `skill`.

Mark a type `keyed: true` with `keys` when a person should have exactly one value per attribute; reconcile then updates by key instead of comparing text.

### 7.3 Choosing types for a new domain

| You want to remember | Use | Config hint |
|---|---|---|
| Stable attributes of a person (city, role) | `profile`, keyed | `retrieval: always`, `keys` with one-line descriptions, `mutable: false` on identity keys |
| How they want answers | `preference`, keyed | `retrieval: always`, `strict_keys: false` to allow new keys |
| Situation details by topic | `fact` + areas | `area: required`, `areas.seeds`, `max_active` |
| Worked cases | `episode` | `ttl`, `max_active`, `verify_episodes: true` |
| Domain vocabulary shared by all users | `term` | `scopes: [workspace]`; approved via `queue` |
| Reusable procedures | `skill` | `extract: false`; add with `memhub add` |

## 8. Operations

- **Embedding change:** edit `embeddings`, run `memhub reembed` (needs `workspace_admin`). Other commands refuse to run until it finishes. Changing chat models needs no migration.
- **Upgrades:** `memhub init` is idempotent and adds new columns; there is no down-migration. Back up the DB first. Old rows are validated by their stored `schema_version`.
- **Retention:** dropped-candidate payloads are purged after `retention.dropped_days` (90) at the end of each ingest; memory rows are never auto-deleted or auto-archived (stale rows are only hidden).
- **Cost:** per run, ≤ 1 extractor call per slice per pass, ≤ 1 judge call per candidate reaching reconcile plus optional verify/repair calls, 1 judge call per touched area, 1 embedding per candidate and per query. Tokens are in the run summary; `cost_usd` is not computed.
- **Privacy:** only quotes are stored. `memhub delete --user <id>` hard-deletes a user's memories and run history.
- **Monitoring:** `memhub runs` for `extract_error`, `failed_segments`, and drop reasons. Investigate a rise in `extract_error` (model truncation) first.
- **Multiple projects, one database:** distinct `project_prefix` values give separate table sets.

## 9. Integration checklist

- [ ] Postgres with pgvector reachable; `memhub init` ran; embedding model and dims final.
- [ ] Logs have unique `message_id`, true event timestamps, one `thread_id` per conversation, and an authenticated `user_id`.
- [ ] `extraction.instructions` written for the domain; types, keys, and area seeds chosen.
- [ ] `--dry-run` reviewed: `memhub runs` drop reasons make sense; sample of 50 memories judged for precision and quote support.
- [ ] Ingest scheduled, single instance.
- [ ] Reviewer with `workspace_admin` role for `queue`.
- [ ] Agent side: `user_id` comes from auth, not from the model; injected `memory_id@version` logged; prompt marks unverified/inferred items.
- [ ] LangChain ≥ 1.0 and a checkpointer if using the middleware.
- [ ] Erasure path documented (`delete --user`).
