# 38 — Adapting memhub to other projects (support, maintenance, Azure, an existing database)

**What was checked:** two real projects built outside the Habitantes config (`examples/support`, `examples/maintenance`) and
probes for each integration point. Support ran end to end on the shared Postgres: SQL source with a tenant per row, its own
schema in an existing database, types declared in the yaml, `/remember save this ticket`, search per tenant. Probes are in
`pilot/adapt/`, tests in `tests/test_adapt.py`.

| Question | Before | Now |
|---|---|---|
| **An existing database: are tables saved correctly?** | Worked in the `public` schema (prefix-named tables; the project's own tables untouched, re-`init` idempotent). A non-`public` schema failed (`vector type not found`). A prefix with a dash, a space or `;` broke SQL, or was an injection surface. | `database_schema` (created if missing; `public` stays on the path for the vector extension). The prefix and schema are validated as plain identifiers. |
| **Traces live in the project's own table** | Only JSONL files and MLflow. | `kind: sql` (read-only, streamed, same field mapping as JSONL, optional feedback query). `kind: "package.module:Class"` plugs in any adapter (Zendesk, Langfuse, ...); extra yaml keys reach it. |
| **Several customers / tenants in one table** | One workspace per source. | `fields.workspace_id` names the tenant column; verified: acme and globex rows land in their own workspaces and never leak into each other's search. |
| **Azure OpenAI, other models, other embeddings** | Chat worked through `params`. Embeddings ignored `base_url` (gateways, LiteLLM, vLLM unusable). `api_key_env` was mandatory (Ollama, Bedrock, managed identity). No extras for other providers. | `base_url` on embeddings (and `azure_endpoint` for Azure), `api_key_env` optional, `factory: "module:function"` builds any model yourself, extras `google`, `bedrock`, `ollama`, `mistral`. `memhub init` embeds one text and refuses a wrong `dims`. Switching models is editing the yaml (a new embedding model: `memhub reembed`). |
| **Schemas: maintenance, customer support** | A class had to be importable, and a class beside the yaml was not (`ModuleNotFoundError`). | The yaml's folder and cwd are on `sys.path`. Or declare the type in the yaml, no Python: `fields: {equipment: str, fix: "str?"}` with descriptions, `content_template`, `on_remember`. |
| **A search tool for any project** | Only LangChain `MemoryMiddleware`. Search/propose were generic (any type, any tenant), but nothing for other stacks. | `memhub.toolkit.MemoryToolkit`: `search_memory` / `propose_memory` as callables and OpenAI-style tool specs, `call(name, args)`; the tenant and user are fixed by the host. Tools work for any type because `content` carries the composed text. |
| **Two workers or an overlapping cron on the same thread** | No lock: both would read the same watermark and write the rows twice. | Per-thread Postgres advisory lock; the second worker skips the thread (`threads_locked`). |
| **Who reviews shared memories** | Workspace-scope rows always waited for an admin. | Still the default; `review: false` on a type makes its workspace rows active at once (a support team's own tickets). |

**Already fine (checked):** per-user erasure and retention (`memhub delete --user`, `retention.dropped_days`), tenant and user
isolation in search, roles through `Actor`, per-project extraction and `/remember` instructions, `${ENV}` in the yaml,
`sslmode=require` in the URL, several projects in one database by prefix.

**Not covered (needs work if you want it):** Postgres with pgvector only (no SQLite, Cosmos or Mongo); cost in dollars
(`cost_usd` is empty, tokens are counted); an HTTP or MCP server around the toolkit; the SQL source rereads its query
each run (put a date filter in it for a very large table); Azure AD tokens need a `factory`.

- [x] `pytest` green, including `tests/test_adapt.py`
- [x] Support example ingested from a SQL table in an existing database, per-tenant search checked
