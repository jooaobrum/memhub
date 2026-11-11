# Memory facade with your own connection and models - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/memory-facade/plan.md`

50 checks in 7 slices · 6 one-way doors · 0 open

## Checks

### S1 - Build `Memory` from the app's own objects

**C1** - `Memory(db=pool, llm, embedder)` with no config has prefix `memhub`, scopes `["user","workspace"]`, the 8 built-in types, and borrows no connection and calls no model (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_defaults_without_config_touch_nothing`

**C2** - `init()` creates nullable `run_id` / `agent_id`, stores dims 16 from one probe embed; a second `init()` changes nothing; an old ledger with rows gains both columns, rows null (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_creates_columns_and_probe_dims`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_is_idempotent`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_upgrades_previous_ledger`

**C3** - A call on a database where `init` never ran raises `EmbeddingMismatch` whose message contains `init()` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_call_before_init_names_init`

**C4** - Pool, `psycopg.Connection`, callable and Postgres URL each pass `add` then `search` with the added memory first (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_add_then_search_through_each_db_kind`

**C5** - A `sqlite3.Connection` or `sqlite:///` URL raises `NotImplementedError` naming `sqlite` (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_sqlite_is_not_implemented_yet`

**C6** - `db=42` and a MySQL URL raise `TypeError` naming the accepted kinds (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_unknown_db_kind_is_type_error`

**C7** - `config` as path, dict or `Settings` sets types/scopes/roles/prefix, and its `database_url` / `llm` / `embeddings` are ignored (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_config_behaviour_used_db_and_models_ignored`

**C8** - With no `judge`, the judge is the `llm` object (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_judge_defaults_to_llm`

**C9** - An embedder with a different model id or dims than `init` stored makes later calls raise `EmbeddingMismatch` (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_other_embedder_is_embedding_mismatch`

### S2 - Borrowed connections stay the app's

**C10** - After `add`, a failing `add` (`InjectionDetected`) and a failing `get` (`NotFound`), the pool has `closed is False` and `SELECT 1` through it returns 1 (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_pool_open_after_every_call`

**C11** - The same three calls leave a borrowed `psycopg.Connection` open and usable (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_connection_open_after_every_call`

**C12** - Inside the app's open transaction, a failing memhub call keeps the app's uncommitted row visible and the transaction open (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_failure_rolls_back_only_own_savepoint`

**C13** - Every connection a callable returned is closed after the call; 3 calls -> all closed (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_callable_connections_closed`

**C14** - 5 calls through a pool of size 1 run `register_vector` once and `SET search_path` once (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_prepare_once_per_connection`

**C15** - After `thread_lock` exits on a pool, another session acquires the same advisory lock, and the pooled connection is open (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_pool_thread_lock_released`

**C16** - `thread_lock` on a borrowed connection takes and releases the lock on it and leaves it open (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_connection_thread_lock_released`

### S3 - Required ids and bound clients

**C17** - Each id missing, `None` or `""` on every `Memory` method raises `ValidationError` naming it, with zero connections borrowed and zero model calls; `agent_id` omitted succeeds (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_missing_ids_raise_before_any_work`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_agent_id_is_optional`

**C18** - A `scope(...)` client's methods take no ids and write the same rows as the unbound call (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_scope_binds_ids`

**C19** - `scope(...)` without one of the three ids raises `ValidationError` at `scope(...)` (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_scope_requires_ids`

**C20** - Rows written by `add`, `update`, `promote` carry the call's `run_id` and `agent_id` (null when omitted) (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_rows_carry_run_and_agent_id`

**C21** - `get` / `history` on another user's memory or another workspace's memory raise `NotFound`; a `workspace_admin` of that workspace reads it (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_get_and_history_hide_other_owners`

**C22** - Default roles get `PermissionDenied` on `approve` / `reject`; `workspace_admin` succeeds (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_review_needs_approving_role`

### S4 - mem0-style methods over `MemoryService`

**C23** - A user-scope `add` returns `AddResult(run_id, [MemoryEvent ADD active v1], dropped=[])` (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_user_scope_is_add_event`

**C24** - A workspace `add` is `PENDING`/`candidate` for a user, `ADD`/`active` for a `workspace_admin` (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_workspace_event_by_role`

**C25** - `add("text")` stores a `fact` with that content (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_text_is_fact`

**C26** - `add(..., infer=True)` raises `NotImplementedError` naming push ingestion, with no model call (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_infer_true_not_implemented`

**C27** - `search(top_k=k)` returns at most k rows, only own active user rows and active workspace rows, no stale, no other user's (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_search_own_and_workspace_rows_only`

**C28** - `get_all()` returns own active user rows and workspace active rows; `status="candidate"` returns candidates (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_get_all_by_status`

**C29** - `update` returns `UPDATE`/`active`, version + 1, same `id` (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_update_returns_update_event`

**C30** - `delete(id)` archives (then `get` raises `NotFound`); `delete(id, hard=True)` removes the row under today's rules (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_delete_archives_and_hard_deletes`

**C31** - `delete_all()` erases the caller's memory and run rows, returns `{"deleted","runs"}` counts, and leaves other users' rows (AC 31)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_delete_all_erases_only_caller`

**C32** - `queue()` returns the workspace's candidates, conflicts first, none from another workspace (AC 32)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_queue_workspace_candidates_conflicts_first`

**C33** - `approve` / `reject` take a `memory_id` (newest candidate) or a `version_id`; a `memory_id` with no candidate raises `NotFound` (AC 33)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_approve_reject_accept_memory_or_version_id`

**C34** - `promote` returns `PENDING`/`candidate`/`workspace` with a new `id` (AC 34)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_promote_returns_pending_workspace_event`

**C35** - `middleware()` / `tools()` are built on the same `MemoryService`, the toolkit bound to the call's workspace and user (AC 35)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_middleware_and_tools_share_service`

**C36** - `propose_memory` in the toolkit and the middleware returns JSON `{"id": <memory_id>, "event": "PENDING", "status": "candidate"}` (AC 36)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_toolkit_propose_returns_id_event_status`
Proof: `.venv/bin/python -m pytest tests/test_middleware.py::test_propose_memory_tool_returns_id_event_status`

**C37** - `ingest()` with one configured source returns a `RunSummary` with a `run_id`; with none it raises `ValidationError` (AC 37)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_ingest_runs_single_source`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_ingest_without_source_raises`

### S5 - One result format, no embeddings out

**C38** - `MemoryEvent` has exactly `id, version_id, version, event, status, scope, type, content, conflicts_with`, and `id` is the `memory_id` (AC 38)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_memory_event_fields`

**C39** - No `Memory` method and neither tool returns a key `embedding` at any depth (AC 39)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_no_embedding_in_any_return`

**C40** - Unknown type, injection, missing id and permission each raise and return no `AddResult` (AC 40)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_invalid_calls_raise`

**C41** - `AddResult.model_dump()` is accepted by `json.dumps` (AC 41)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_result_dumps_to_json`

### S6 - Nothing existing breaks

**C42** - Today's suite passes, with only the `propose_memory` output assertions changed (AC 42)
Proof: `.venv/bin/python -m pytest tests`

**C43** - A `MemoryStore` built from a URL string opens and closes one connection per transaction (AC 43)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_url_opens_and_closes_per_transaction`

**C44** - The CLI with a yaml lacking `database_url` exits non-zero naming `database_url` (AC 44)
Proof: `.venv/bin/python -m pytest tests/test_cli.py::test_cli_without_database_url_names_key`

**C45** - `memhub` exports `Memory`, `AddResult`, `MemoryEvent`, `Dropped`, `CallContext`, `ValidationError` (AC 45)
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_public_exports`

**C46** - `pyproject.toml` requires `langchain>=1.0` and `psycopg-pool` (AC 46)
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_pyproject_dependencies`

**C47** - `docs/azure.md` shows an Entra-token `ConnectionPool` with `max_lifetime` < 3600 and Azure models with `azure_ad_token_provider`; nothing under `src/memhub` imports `azure` (AC 47)
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_azure_recipe`
Proof: `.venv/bin/python -m pytest tests/test_packaging.py::test_core_imports_no_azure`

**C48** - The demo: pool, fake models, `init` -> `add` -> `search` -> `promote` -> `approve` -> `delete_all` succeed and the user's rows are gone; a call with no `user_id` raises `ValidationError` (AC 48)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr1_memory.py::test_pr1_demo`
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr1_memory.py::test_call_without_user_id_fails`

### S7 - Found by verification

**C49** - A `SET search_path` undone by a rollback (memhub's own failure, or the app's rollback around memhub's first call) is run again on the next call (AC 49)
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_rolled_back_search_path_is_set_again`
Proof: `.venv/bin/python -m pytest tests/test_connection_source.py::test_app_rollback_does_not_lose_the_schema`

**C50** - `ingest()` with one message through a pool of `max_size=1` completes with `threads_seen == 1` and a run row, no `PoolTimeout` (AC 50)
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_ingest_through_pool_of_one`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| `db` kinds (7) | `ConnectionPool` C4 C10 · `psycopg.Connection` C4 C11 · callable C4 C13 · Postgres URL C4 C43 · `sqlite3.Connection` C5 · `sqlite:///` C5 · other C6 | - |
| `Memory` methods needing ids (15) | `add` C17 · `search` C17 · `get` C17 · `get_all` C17 · `update` C17 · `delete` C17 · `delete_all` C17 · `history` C17 · `queue` C17 · `approve` C17 · `reject` C17 · `promote` C17 · `middleware` C17 · `tools` C17 · `ingest` C17 | - |
| id fields x empty forms (9) | `workspace_id` missing C17 · `workspace_id` None C17 · `workspace_id` empty C17 · `user_id` missing C17 · `user_id` None C17 · `user_id` empty C17 · `run_id` missing C17 · `run_id` None C17 · `run_id` empty C17 | - |
| write events produced in this PR (3) | `ADD` C23 · `PENDING` C24 C34 C36 · `UPDATE` C29 | - |
| statuses returned (4) | `active` C23 C33 · `candidate` C24 · `rejected` C33 · `archived` C30 | - |
| Landing doors (6) | 1 columns C2 C20 · 2 API and models C38 C41 C45 · 3 tool output C36 · 4 ConnectionSource C10-C16 · 5 settings defaults and embedding id C1 C7 C9 · 6 dependencies C46 | - |
| `approve` id forms, AC 33 (3 examples) | `memory_id` C33 · `version_id` C33 · `memory_id` with no candidate C33 | - |
| AC 17 examples (5) | `user_id=None` C17 · `user_id=""` C17 · `agent_id` omitted C17 · `search` no `run_id` C17 · `approve` no `user_id` C17 | - |
| AC 10 call outcomes (3) | `add` ok C10 C11 · `InjectionDetected` C10 C11 · `NotFound` C10 C11 | - |
| startup assemblies (3) | `Memory` C1 · CLI `_service` C44 · test harness `conftest` C42 | - |
| readers of tool output (2) | toolkit C36 · middleware C36 | - |

- Claims naming an exception or a returned shape: C3, C5, C6, C17, C19, C21, C22, C26, C33, C37, C38, C40 - each proof calls the method and asserts the exception type or the field set

## Swept

- validation: C17, C19, C40, C6
- failure modes: C10, C11, C12, C49
- idempotency: C2
- authorization: C21, C22, C31
- concurrency: C15, C16, C50
- data lifecycle: C30, C31, C2
- dependency failure: C3, C9
- state transitions: C23, C24, C29, C33, C34
- observability: n/a - no logging requirement; `run_id` / `agent_id` on rows are the audit trail (C20)

## Handoff

- **Settled mid-build:** user: "push to the branch of the issue" - pushed to `1-memhub-as-a-pluggable-memory-package` (created linked to #1 with `gh issue develop`)
- **Dispatched:** Verifier at `da0aa17` (code at `fdec080`), diff `e93c070..da0aa17` - returned FAIL, 7 findings
- **Boundary:** C1-C48 closed in the feature commit on `feat/memory-facade`; full suite 598 passed (398 existing + 200 new)
- **Abandoned:** `conn.transaction()` for connections memhub opens itself - it forbids the explicit `conn.rollback()` existing code does inside `store.connect()` (`test_reembed_retypes_the_column_and_clears_old_vectors`); owned connections keep today's commit/rollback
