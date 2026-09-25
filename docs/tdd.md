# memhub — Technical Design Document

Status: v1.2 implemented (tickets 01–37). Source of truth for intent: `.specs/features/memhub/` (spec, design, research). This document describes **what the code does today** and why; where code and spec differ, the code wins and the difference is listed in [§12](#12-spec-vs-code-differences).

Related: [architecture.md](architecture.md) (structure and diagrams) · [integration.md](integration.md) (adopting memhub in a project).

## 1. Problem and goals

Agents forget between conversations, and auto-writing memory frameworks give no guarantee that what they store is true. memhub is a small, standalone Python package that:

- turns conversation traces into **typed, evidence-backed, versioned** memories;
- never publishes shared (workspace) knowledge without human approval;
- serves memories back to any agent through a Python API, a LangChain middleware, or a CLI.

Design principle (v1.2): **less is more**. A small ledger where every row has a verbatim quote beats a large one that invents. Most guardrails exist to keep the row count low.

**Non-goals (v1):** web UI or HTTP API, graph database, cross-thread behaviour mining ("Loop 2"), automatic decay or deletion of stale rows, term discovery by co-occurrence, "as-of" queries beyond reading the version chain.

## 2. Requirements that shape the design

| # | Requirement | Design consequence |
|---|---|---|
| R1 | Every memory is traceable to what a user/tool said | Verbatim `quote` per evidence entry; mechanical substring check (`ground.py`); no quote, no memory |
| R2 | The extractor must not overwrite human decisions | `verified` flag; reconcile turns any differing statement against a verified row into a conflict candidate |
| R3 | Time-correct | `observed_at` = newest evidence message time (never ingestion time); out-of-order ingestion never regresses a value |
| R4 | Ingestion is idempotent and crash-safe | One transaction per segment, watermark stored in the same transaction; evidence deduped by `message_id` |
| R5 | Provider-agnostic | LLMs and embeddings built via LangChain `init_chat_model` / `init_embeddings` from config |
| R6 | Reusable across projects | Types, keys, areas, caps, sources, prompts are config; code contains no project vocabulary |
| R7 | Privacy | Only quotes are stored, not transcripts; per-user hard erase |

## 3. System overview

Two loops share one Postgres ledger:

- **Write path (batch):** `source → segment → prefilter → extract → ground → score → reconcile → route`, then one area-summary pass per run. Driven by `memhub ingest` or `ingest_source()`.
- **Read path (online):** `MemoryService.search / page / list` and `MemoryMiddleware` for LangChain agents.
- **Review path (human):** CLI (`queue`, `approve`, `reject`, `edit`, `archive`, `delete`, `add`, `areas`).

See [architecture.md](architecture.md) for the diagrams.

## 4. Data model

### 4.1 Tables

Prefix `<p>` is `project_prefix` from config, so several projects can share one database.

| Table | Purpose |
|---|---|
| `<p>_memory` | The ledger. One row per **version** of a memory. |
| `<p>_memory_runs` | One row per processed segment: watermark, signals, dropped candidates with reasons, token counts. |
| `<p>_memhub_meta` | Key/value. Stores `embedding = {model, dims}` written by `init`; every command refuses to run if config no longer matches. |

`<p>_memory` columns (see `store.py:init`):

- Identity: `id` (version id), `memory_id` (stable across versions), `version`, `UNIQUE(memory_id, version)`.
- Classification: `type`, `schema_version`, `scope` (`user`|`workspace`), `workspace_id`, `user_id`.
- Lifecycle: `status` (`candidate|active|rejected|archived|superseded`), `verified`, `reviewed_by/at`, `review_note`, `conflicts_with` (a `memory_id`).
- Content: `content` (the embedded statement), `payload` (jsonb, validated by the type class at its own `schema_version`), `entities`, `embedding vector(dims)`.
- Provenance: `evidence` jsonb `[{source, trace_id, thread_id, message_id, observed_at, quote, claim_source}]`, `seen_count` (independent threads only), `created_by` (`extractor` | `summarizer` | actor id), `score`.
- Time: `observed_at`, `valid_from`, `valid_until`, `durability` (`stable|ongoing|temporary`), `assertion` (`stated|inferred`).
- Relations: `links` jsonb `[{kind, memory_id}]`. Only `kind = in_area` is written.

Indexes: unique `(memory_id) WHERE status='active'` (one active version per memory); unique `(workspace_id, scope, user_id, type, payload->>'key') WHERE status='active' AND key IS NOT NULL` (one active row per slot); btree on `(workspace_id, scope, status, type)` and `user_id`; GIN on `entities` and `links`; HNSW on `embedding` (cosine).

`memhub init` is idempotent DDL: later-added columns use `ADD COLUMN IF NOT EXISTS` with backfill. There is no migration tool.

### 4.2 Memory types

Built-ins (`types.py`), each a Pydantic subclass of `MemoryBase(content, entities, tags)`:

| Type | Fields | Role |
|---|---|---|
| `profile` | `key` | Who the person is. Core. |
| `preference` | `key` | How they want the agent to answer. Core. |
| `fact` | – | A situation statement, always inside an area. Core. |
| `episode` | `situation, actions, outcome` | A real case. Secondary. |
| `term` | `term, expansion, aliases, related, confidence, confirmed_by_user` | Shared glossary entry (workspace scope). Secondary. |
| `area` | `title, description, summary, proposed, key, …` | Page header; created by the pipeline, never proposed by the extractor. |
| `skill` | `name, description, body` | Procedural memory, index-then-load. Off for extraction. |

Projects register their own via `types.<name>.class: "module:Class"`. `TypeRegistry` maps `(type, schema_version) → class`; a stored row is always validated with the class of its own version, so old rows are never silently reinterpreted.

**Keyed types (slots).** A type with `keyed: true` allows one active row per `(owner, type, key)`. `keys` is a closed list (`strict_keys: true`, extractor sees it as an enum) or suggestions only (`strict_keys: false`). A key can be `mutable: false`.

### 4.3 Lifecycle

```
candidate ──approve──▶ active ──archive──▶ archived
    │                    │
    └──reject──▶ rejected └──edit / supersede──▶ superseded (new version N+1 is active)
```

| Operation | Effect |
|---|---|
| approve | `candidate → active`, `verified = true`. For a conflict candidate the reviewer picks `keep_old`, `replace`, or `keep_both` (keyed types: merged into one statement) |
| edit | Insert version N+1 as `active`, `verified = true`; N becomes `superseded`; one transaction |
| archive | `active → archived` |
| delete | Hard delete; only rejected rows, or the actor's own user-scope rows |
| delete --user | Hard-erase every user-scope row and the user's run rows |
| promote | Copy a user-scope memory into the workspace queue as a new candidate |

There is no per-action event log; history is the version chain (`memhub history`).

**Stale is computed, not stored:** `status='active' AND valid_until < now`. Search and the middleware skip stale rows; nothing mutates when a memory expires.

## 5. Write path (ingestion)

Entry: `ingest_source()` in `pipeline/ingest.py`. Each stage below can drop a candidate; every drop is recorded with a reason in `<p>_memory_runs.dropped`.

### 5.1 Segmenting (`segment.py`)
- Group interactions by `thread_id`, sort by timestamp.
- **Segment:** messages newer than the thread watermark, processed only when the thread's newest message is older than `now − segment_idle` (default 1h). Long segments are cut into slices of `segment_max_user_turns` (default 8) user turns.
- **Final pass:** once per thread, when idle ≥ `thread_close` (default 7d): the whole thread, offered to the extractor for Episode-like types only.
- A segment whose processing raises is rolled back, counted as `failed_segments`, and the thread stops there so a later segment cannot advance the watermark past it.

### 5.2 Prefilter (`prefilter.py`)
No LLM. Skip when the segment has fewer than `min_user_turns` user turns, or every turn matches `skip_when` (e.g. `intent: [greeting, out_of_scope]` from message metadata). Signals are still recorded.

### 5.3 Extract (`extract.py`)
One structured-output call per slice (`extraction.passes` samples, united). Output schema is generated from config: a discriminated union over enabled types (`extract: true`), keyed types get `key` as an enum, area types get `areas` (`{existing: key}` or `{new: {title, description}}`; `new` removed when `areas.open: false`). Each candidate carries `evidence[{message_id, quote, claim_source}]`, `utility 1–5`, `durability`, `assertion`, optional `valid_from/valid_until`. Transcript lines are rendered `[#id date] role: text`, so the model resolves relative time against the message date. Prompt = code mechanics + `extraction.instructions` (the per-project domain policy). Max `ingestion.max_candidates` per slice. Unparseable output is retried `extraction.retries` times, then recorded as `extract_error` with zero candidates.

### 5.4 Ground (`ground.py`)
Rule checks, first failure drops the candidate:

| Reason | Rule |
|---|---|
| `invalid_fields` | Type unknown, Pydantic validation fails, or unconfigured entity type |
| `ungrounded` | No evidence; quote not a substring of the message (whitespace/case-normalised); `claim_source` disagrees with the quoted message's role; term alias missing from quote |
| `low_confidence` | Model-defined term below `terms.min_confidence` (0.9) |
| `unknown_key` | Keyed type, strict keys, key not in list |
| `assistant_only` | Fact whose evidence is all `assistant` |
| `injection` | Prompt-injection regex match in any string field or area |
| `no_area` | Type with `area: required` and no area |
| `question_episode` | Episode retelling a question or an assistant action |
| `relative_time` | Non-episode content containing "next year", "recently", … |
| `too_thin` | Fact/profile under two words |
| `need_or_request` | Fact/profile phrased as a need ("wants…", "precisa…") without habitual marker |
| `inferred` | `assertion = inferred` and `guardrails.allow_inferred` is false (default) |

Optional judge-model checks (all default off): `repair_relative_time` dates a relative claim instead of dropping it; `verify_episodes` (`unverified_episode`); `verify_claims` (`unsupported_claim`).

Then `same_slot_in_segment`: for several candidates on one slot, only the one from the latest message continues.

### 5.5 Score (`score.py`)
`score = Σ wᵢ·fᵢ` over `admission.weights`, each fᵢ ∈ [0,1]:

| Feature | Value |
|---|---|
| `utility` | (utility − 1)/4 |
| `evidence` | 1.0 stated with user/tool evidence · 0.6 inferred · 0.4 assistant-only |
| `novelty` | 1 below the conflict band's lower bound, falling linearly to 0 at `reconcile.duplicate`, based on the max similarity to active rows of same type/scope/owner |
| `type_prior` | per-type config |
| `signals` | 0.5, +0.5 correction or thumbs-up, −0.5 thumbs-down/rephrase/error; clamped |

Below `admission.threshold` → `low_score`.

### 5.6 Areas (`areas.py`, before reconcile)
For types with `area` set, candidates resolve to 1–3 area rows of the same owner: `{existing: key}` creates the seed area row on first use; `{new}` is embedded and merged into an existing area at ≥ `areas.merge_similarity` (0.88), else created with `proposed = true`; over `areas.max_per_user`/`max_per_workspace` → `area_cap`.

### 5.7 Reconcile (`reconcile.py`)
Decision only; `route.py` writes. Outcomes: `create | merge | supersede | conflict | amend | drop`. The judge (`llm.judge`) is called at most once per candidate, never when normalised texts are equal.

**Keyed candidate** (no similarity search): find the active row M of the slot, then apply in order:

| # | Condition | Outcome |
|---|---|---|
| 1 | Same text, or this segment's message ids already in M's evidence | merge (evidence only) |
| 2 | Same statement already pending against M | merge into the pending candidate |
| 3 | C older than M (`observed_at`) | drop `outdated` |
| 4 | M verified | conflict candidate; M stays active |
| 5 | C inferred, M stated | drop `inferred` |
| 6 | M stale and key mutable | supersede |
| 7 | Judge verdict: `same` → merge · `extends` → supersede with merged content (only if the merged text uses words from both statements, else conflict) · `updates` and key mutable → supersede · `updates` on immutable key, `conflicts`, or unreadable verdict → conflict |

**Non-keyed candidate:** nearest rows of same type(s)/scope/owner sharing an area (or with no area). Equal text or similarity ≥ `duplicate` (0.92) → merge. Otherwise rows with similarity ≥ `compare_floor` (0.30) and matching entities go to the judge together (max `compare_max`); `unrelated` → create; else rules 3–5 above, then the verdict table. A target that is not an active row of the same type always yields a conflict (a person decides).

**Terms** use a separate path: same term (case-insensitive) adds aliases up to `terms.max_aliases`; an alias owned by a different active term is a conflict for the admin.

An unreadable judge output is treated as `conflicts`: it lands in review; nothing is overwritten.

### 5.8 Route (`route.py`)
- `observed_at` = newest evidence timestamp. `valid_until` = candidate's own date → `types.<t>.ttl` → `ttl[durability]` → null.
- User scope → `active`, `verified=false`. Workspace scope, or any conflict → `candidate`.
- `supersede` uses `edit_memory` (version N+1, `created_by='extractor'`, keeps old evidence, adds new).
- `merge` appends deduped evidence, increments `seen_count` only for a new thread, sets `valid_until` to the later of the two (null is latest), and appends missing `in_area` links.
- Caps: `max_active` per (owner, type) → `cap_reached`; `terms.max_per_workspace` → `term_cap`.

### 5.9 Summaries and retention
After the last segment (skipped on `--dry-run`): every area that gained or changed a row gets one `llm.judge` call. The prompt contains only the area title and its active linked rows' `content`; the result is stored as a new version with `created_by='summarizer'`. Skipped when the linked row set fingerprint is unchanged or the area has no active rows. Then `purge_dropped` deletes `dropped` payloads older than `retention.dropped_days` (90).

### 5.10 Transaction and failure model
- One DB transaction per segment covers memory writes and the run row (watermark). LLM/embedding failure ⇒ exception ⇒ rollback ⇒ retried next run.
- Area summaries run after all segments, one transaction each; a failure loses only that summary.
- `--dry-run` runs the whole segment logic then rolls back (LLM calls are still made and cost tokens).
- Idempotence: re-running with no new lines makes no LLM calls; reprocessing the same segment creates no versions (evidence dedup by `message_id`).

## 6. Read path

### 6.1 `MemoryService.search` (`service.py`)
1. **Glossary expansion:** active workspace `term` rows whose `term` or alias occurs in the query (word-boundary, case-insensitive) append their `expansion` and aliases to the embedding text. `related` adds no text.
2. Embed once; guard embedding config.
3. **Area boost:** the owner's area named by `area`, else the nearest area row by embedding. Rows linked to it get +0.05 similarity (capped at 1.0). Never a filter.
4. SQL: `status='active'`, scope/owner visibility (`workspace` rows of the workspace + `user` rows of `user_id`), stale excluded unless `include_stale`, `type<>'area'` unless type given, order by cosine distance, `LIMIT max(4k, 20)`; re-rank with boosts (`related` words are a text boost); return top `k`.
5. Each row carries `areas` (titles) and `expanded_by`.

Workspace-scope candidates are never returned: only `active` rows are searched.

### 6.2 Pages
`page(owner, area)` is a query, not a stored document: area title + derived summary + active, non-stale linked rows newest first (cap `areas.page_max_items`) + `last_updated` = newest `observed_at`. `page_for_query` returns the nearest area's page only when similarity ≥ `areas.page_min_similarity` (0.5).

### 6.3 Middleware (`middleware.py`)
LangChain `AgentMiddleware`:
- `before_agent`, first invocation of a state: build the **snapshot**: one `<about_the_user>` block from all `retrieval: always` types (Profile first; each type trimmed to `max_chars`, whole entries only), plus `<skill_index>` for `index_then_load` types. Stored in state (`memory_snapshot`).
- `before_agent`, every invocation: `search` on the last human message (`k`, default 5) → `<memory>` block; plus the nearest `<area_page>` within `areas.page_max_chars`. Stored as `memory_turn`.
- `wrap_model_call`: appends snapshot + turn to the system message for that request only; history is untouched.
- Trace: every injected `memory_id@version` is accumulated in state `memhub_injected` and, if an MLflow trace is active, written to trace metadata `memhub_injected`. memhub stores nothing else for this.
- Tools: `search_memory(query, type?)`, `load_skill(name)`, `propose_memory(type, fields, evidence, scope='user')`. `propose_memory` always creates a `candidate`, even for user scope.
- Line format: `[<version id>|<type>|verified|unverified] content (as of YYYY-MM-DD[, inferred]) (sources)`.

## 7. Authorization

memhub has no authentication. Callers pass an `Actor(id, roles)`.

| Action | Requirement |
|---|---|
| approve / reject | a role in `roles.approve_workspace` (default `workspace_admin`) |
| edit / archive user memory | owner or `workspace_admin` |
| edit / archive workspace memory | `workspace_admin` |
| delete | rejected row (owner or admin) or own user-scope row |
| delete --user | that user or `workspace_admin` |
| reembed / purge | `workspace_admin` |
| `add` | user scope: active + verified; workspace scope: active only for admins, else candidate |
| middleware | acts as `Actor("agent")`, no roles; can only search and propose |

The CLI acts as `cli:<os user>` with roles from `MEMHUB_CLI_ROLES` (default `workspace_admin`).

## 8. Configuration

One YAML per deployment (`memhub.yaml`, `${ENV}` interpolation over the raw text; unset variable = `ConfigError`). Schema is `config.Settings`. Groups:

| Key | Controls |
|---|---|
| `project_prefix`, `database_url`, `workspace_default` | Table names, DSN, implicit workspace |
| `llm.extractor`, `llm.judge`, `embeddings` | `{provider, model, base_url?, api_key_env, params?}` (+ `dims`) |
| `scopes`, `entity_types` | Enabled scopes; allowed `{type,id}` entity types |
| `types.<name>` | `class, retrieval (search|always|index_then_load), type_prior, max_chars, max_active, extract, ttl, keyed, keys, strict_keys, area (required|optional), scopes` |
| `areas`, `terms` | Seeds, caps, merge/page thresholds; term caps and confidence |
| `sources.<name>` | `kind: jsonl|mlflow`, field mapping, signals file |
| `ingestion`, `extraction`, `admission`, `reconcile`, `guardrails`, `ttl`, `retention`, `roles` | Pipeline knobs (defaults in `config.py`) |

A reference file is `memhub.example.yaml`. Provider switch is config-only; the provider's LangChain package must be installed (`memhub[openai]`, `memhub[anthropic]`). Model construction fails fast if a chat model lacks structured output.

**Embeddings:** the `vector(dims)` column is fixed by `init`. Changing model or dims requires `memhub reembed` (resizes the column, recomputes every row, rebuilds the HNSW index). Anthropic has no embedding model; use OpenAI, Voyage, or a local model.

## 9. Interfaces

### 9.1 CLI
`init · reembed · ingest --source <n> [--reprocess] [--dry-run] [--thread <id>] · add · list · search · queue · approve · reject · edit · history · archive · delete [<id> | --user] · promote · areas [merge <from> <to>] · page · runs`. Global option `--config/-c` (default `./memhub.yaml`). Output is JSON on stdout (`page` prints text); diagnostics go to stderr.

### 9.2 Python API
`MemoryService` (see [integration.md](integration.md)): `add, edit, archive, delete, delete_user, approve, reject, promote, list, search, expansion, areas, merge_areas, page, page_for_query, pages, queue, runs, history, propose, reembed, purge_dropped`. Errors: `ServiceError` → `PermissionDenied | ValidationError | InjectionDetected`, plus `NotFound`/`StoreError`.

### 9.3 Source adapters
Protocol in `sources/base.py`: `read() -> Iterator[Interaction]`, optional `signals() -> list[dict]`, optional `skipped: int`. `Interaction(thread_id, user_id, workspace_id, message_id, role, content, timestamp, trace_id, metadata)`. Built in: field-mapped JSONL (+ feedback file joined on `message_id`/`thread_id`) and MLflow traces (one trace = one user + one assistant message; `message_id = trace_id`, assistant `trace_id:a`).

## 10. Security and privacy

- **Injection:** regex scan (`injection.py`) over every string field of extractor output, `add`, `edit`, and `propose`.
- **Grounding:** mechanical, not model-judged: quote substring + claim-source/role agreement.
- **Data minimisation:** only quotes are stored. Per-user erase satisfies the erasure requirement.
- **Trust boundary:** whoever calls `MemoryService` is trusted to supply a truthful `Actor`; the middleware's `user_from` must come from an authenticated identity, since it selects whose memories are injected.
- **Secrets:** API keys come from environment variables named in config (`api_key_env`), `.env` is loaded by the CLI.

## 11. Testing

`tests/` (pytest). Ledger tests start a `pgvector/pgvector:pg16` container through fixtures (Docker required; skipped otherwise). LLM and embeddings are fakes (`tests/fakes.py`), so tests are deterministic. Coverage areas: config, types, store lifecycle, JSONL/MLflow sources, pipeline units, end-to-end ingest, upsert scenarios over time (18→19, out-of-order, verified rows, stale rows, immutable keys, `extends`, re-ingest) with ledger invariants checked after every step, seeded randomised sequences, terms, areas, service, middleware, CLI. Time-dependent code takes `now` as a parameter.

`pilot/` holds manual real-LLM runs on the Habitantes logs and `functional_check.py` (PASS/FAIL of every command). Pilot quality targets: extraction precision ≥ 0.8 on a labelled sample, zero rows without a quote, ≤ 2–3 active memories per user on average.

## 12. Spec vs code differences

Found while reading the code against `.specs/`. The specs are partly stale; treat this list as the current truth.

| Topic | Spec | Code |
|---|---|---|
| Profile keys | Keyed, closed list of keys | `Profile.key` is optional free text by default; keyed only if the project sets `keyed: true`. Example config leaves Profile unkeyed |
| Preference keys | Closed list | `strict_keys: false` in example config (keys are suggestions) |
| Term creation | Only from explicit user definitions | Also model-defined terms when `confidence ≥ terms.min_confidence` (0.9) and not `confirmed_by_user` |
| Grounding rules | Quote, assistant-only, injection, inferred, unknown_key, alias, no_area | Also `relative_time`, `too_thin`, `need_or_request`, `question_episode`, `low_confidence`, and three optional judge checks |
| Ingestion knobs | – | `passes`, `retries`, `segment_max_user_turns`, `assistant_chars`, `verify_*`, `repair_relative_time`, `reconcile.compare_floor/max/extends/compare_across` |
| Reconcile scope | Top 5 by similarity in band | Judge sees up to `compare_max` nearest rows ≥ `compare_floor`; one call picks the row (`about`) |
| Novelty feature | `1 − max similarity` | Linear from `conflict_band[0]` to `duplicate` |
| Tables | Two | Three (`<p>_memhub_meta`); runs table also has `candidates_proposed, created, merged` |
| Search boost | "rank boost" | +0.05 similarity for area match; `related` text boost |
| Entity boost in middleware | Described | Not wired (`entity_boost` exists in `store.search` only) |
| Package location | `packages/memhub/` | Repo root, `src/memhub/` |

## 13. Known limitations and risks

- **`cost_usd` is never computed** (tokens are). `rephrase` signals are never detected. The correction detector's LLM fallback is off by default.
- **Middleware performance:** `_visible()` lists every active row of a type for the workspace, then filters by user in Python; `before_agent` runs two DB round-trips plus one embedding per turn (`search` + `page_for_query` each embed the query). Fine at pilot scale; not for large ledgers.
- **Snapshot scope:** the "fixed for the thread" snapshot only persists with a checkpointer; without one every `invoke` rebuilds it.
- **Version floor:** `pyproject.toml` declares `langchain>=0.3`, but `middleware.py` imports `langchain.agents.middleware`, `ToolRuntime` and uses `runtime.context` (LangChain 1.x API; the dev environment has 1.2.0). Raise the floor before publishing.
- **Sync only:** the middleware implements sync hooks; the store uses synchronous psycopg with a connection per operation (no pool).
- **Concurrency:** ingest is designed as a single batch process. Two concurrent ingests over the same thread can both pass reconcile before either commits; the unique indexes stop duplicates from becoming active but one run would fail its segment and retry.
- **Injection defence is regex-based**, a coarse first line, not a guarantee.
- **Cheap extractors** truncate JSON and miss facts; mitigated by `retries` and `passes`, at token cost.
- **Language:** correction, need/habitual and relative-time regexes cover Portuguese and English (pilot languages); other languages need additions in `ground.py` and `signals.py`.
- **Open design questions:** link vocabulary beyond `in_area` (`supports`, `outcome_of`, `related`); term discovery from co-occurrence.
