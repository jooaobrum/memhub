# Storage ports and an in-memory backend - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/storage-ports/plan.md`

28 checks in 3 slices · 4 one-way doors · 0 open

## Checks

### S1 - Postgres behind the ports, nothing changes

**C1** - `Capability` has exactly `transactions`, `vector_search`, `json_lookup`, `parallel_ingest`, `schemas`; `isinstance(MemoryStore(...), Backend)` and `isinstance(InMemoryBackend(), Backend)` are `True` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_capability_values_and_backends_satisfy_protocol`

**C2** - On both backends, `uow.memories` / `runs` / `meta` / `vectors` expose the 37 methods door 1 lists, each with the keyword parameters of the `MemoryStore` method of that name minus `cur` (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_repository_methods_match_memorystore_signatures`

**C3** - A clean unit of work is visible to the next; one that adds a row, upserts a run and raises `RuntimeError` leaves `get_by_id` and `get_watermark` `None` and re-raises the `RuntimeError` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_commit_is_visible_and_an_exception_rolls_back`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_commit_is_visible_and_an_exception_rolls_back`

**C4** - `MemoryStore.capabilities` is all five for a pool, a `psycopg.Connection`, a callable and a URL (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_postgres_capabilities_for_every_db_kind`

**C5** - `store.bind(cur)` writes are visible to `cur` before commit, and after `cur`'s transaction rolls back they are gone (bind did not commit) (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_bind_shares_the_cursor_transaction`

**C6** - `service.py`, `memory.py`, `cli.py`, `middleware.py`, `toolkit.py`, `pipeline/*.py` import no `psycopg`, have no parameter named `cur`, and call no `.cursor(` / `.connect(` (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_no_psycopg_outside_the_postgres_backend`

**C7** - `memhub.store` exports every public name it had at `2a58e6c`, each the same object as in its new module (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_store_shim_exports_every_name_from_before_the_ports`

**C8** - The 10 pure helpers and errors import from a module that loads with `psycopg` blocked (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_pure_helpers_import_without_psycopg`

**C9** - The full suite passes on Postgres (AC 8)
Proof: `.venv/bin/python -m pytest -q tests`

**C10** - Every test-file line changed since `2a58e6c` in a pre-existing test file replaces `cur=cur, store=<x>` with `uow=<x>.bind(cur)` (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_old_test_files_change_only_at_pipeline_call_sites`

**C11** - `service.search` on Postgres issues the SQL statements recorded at `2a58e6c`, same text and order, prefix normalized (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_search_sql.py::test_search_issues_the_sql_recorded_before_the_ports`

### S2 - A backend says what it cannot do

**C12** - `InMemoryBackend(schema="tenant")` raises `NotImplementedError` with exactly `schemas is not available on memory: remove database_schema from the config, or use Postgres` (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_missing_capability_message_is_exact`

**C13** - `Memory(db=MEM, embedder=e, config={"database_schema": "tenant"})` raises the C12 error from `__init__`; without `database_schema` it builds (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_memory_init_checks_needed_capabilities`

**C14** - Each capability test in the contract asserts the C12-shaped error where the backend lacks it, and `pytest -rs tests/test_contract_memory.py` reports 0 skipped (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_schemas_capability`
Proof: `.venv/bin/python -m pytest tests/test_storage_ports.py::test_memory_contract_skips_nothing`

### S3 - In-memory backend for app tests with no Docker

**C15** - With `psycopg.connect` patched to raise: `Memory(db=MEM)` init -> add -> search (first) -> promote -> approve (admin) -> get_all (workspace row active) -> `delete_all` returns `{"deleted": 1, "runs": 0}` (AC 13)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_app_flow_on_memory_without_postgres`

**C16** - Two `MEM` do not share rows; two `MEM + "shared"` do; `MEM + "a"` and `MEM + "b"` do not (AC 14)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_memory_urls_isolate_or_share_by_name`

**C17** - A call before `init()` raises `EmbeddingMismatch` containing `init()`; a second `Memory` on the same named URL with an embedder of other dims raises `EmbeddingMismatch` (AC 15)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_init_required_and_dims_checked`

**C18** - Memory, run and meta rows from either backend have exactly the reference keys (plus `stale` / `similarity` where computed), `uuid.UUID` ids and timezone-aware timestamps (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_rows_have_the_reference_keys_and_types`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_rows_have_the_reference_keys_and_types`

**C19** - Setting `content` and appending to `evidence` on a returned row leaves the next `get_by_id` unchanged (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_returned_rows_are_copies`

**C20** - Thread B's unit of work, started while A's is open, waits; it sees A's row after A commits and nothing after A raises (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_units_of_work_serialize_across_threads`

**C21** - A second active row for one `memory_id`, or for one keyed slot, raises `Conflict` on memory (`UniqueViolation` on Postgres) and one active row remains (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_one_active_row_per_memory_and_per_keyed_slot`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_one_active_row_per_memory_and_per_keyed_slot`

**C22** - While `thread_lock("s", "t1")` is held, a second yields `False`; `("s", "t2")` yields `True`; after release `("s", "t1")` yields `True` (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_parallel_ingest_capability`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_parallel_ingest_capability`

**C23** - Search ranks rows at similarity 0.95 / 0.86 / 0.50 in that order with `similarity` within 1e-6 of cosine, on both backends (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_search_ranks_by_cosine_similarity`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_search_ranks_by_cosine_similarity`

**C24** - Search filters: a stale 0.99 row is out, then first with `include_stale`; another user's 0.99 row never returns; another workspace's row never returns; a candidate never returns; `area` rows only with `type="area"`; `type` filters; `k` cuts; area / text / entity boosts add 0.05 capped at 1.0 - on both backends (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py -k "test_search_filters or test_search_boosts"`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "test_search_filters or test_search_boosts"`

**C25** - `ingest_source` over the JSONL fixture with the fake models writes the same memory rows (type, scope, status, content) and run rows (thread, `candidates_proposed`, `created`, dropped reasons) on both backends (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_ingest_writes_the_expected_rows`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_ingest_writes_the_expected_rows`

**C26** - A segment whose unit of work raises leaves no memory row and no run row, and the next ingest processes it (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_failed_segment_leaves_nothing_and_is_retried`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_failed_segment_leaves_nothing_and_is_retried`

**C27** - After `set_embedding_dims(20)` and `reembed`, every row (candidate and superseded included) has a 20-dim vector and meta `embedding` holds the new model and 20 (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_reembed_to_new_dims`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_reembed_to_new_dims`

**C28** - On either backend, a unit of work before `init` raises `EmbeddingMismatch` naming `init()`; on a Postgres database where `init` never ran (no `vector` type) it is that error, not psycopg's `vector type not found` (AC 15; appended mid-build)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py::test_a_call_before_init_names_init`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_a_call_before_init_names_init`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (4) | ports C1 C2 · capability error C12 C13 · `memory` URL C16 · `memhub.store` shim C7 | - |
| capabilities (5) x backends (2) | `transactions` C3 · `vector_search` C23 C24 · `json_lookup` C24 (entity boost) C25 · `parallel_ingest` C22 · `schemas` C14 (memory lacks) C12 | - |
| repository methods (37) | C2, table-driven over all 37 on both backends; behaviour by the contract suite C3 C18-C27 | - |
| `db` kinds for Postgres capabilities (4) | pool C4 · connection C4 · callable C4 · URL C4 | - |
| modules that must not touch psycopg (6) | `service.py` C6 · `memory.py` C6 · `cli.py` C6 · `middleware.py` C6 · `toolkit.py` C6 · `pipeline/*.py` C6 - one AST walk over all | - |
| unit-of-work outcomes (2) | clean exit C3 · exception C3 C26 | - |
| AC 14 URL pairs (3) | `MEM` x2 C16 · `shared` x2 C16 · `a` / `b` C16 | - |
| AC 15 examples (3) | before init C17 C28 · other dims C17 · fresh Postgres database C28 | - |
| AC 18 examples (2) | commit -> seen C20 · raise -> nothing C20 | - |
| AC 19 uniqueness (2) | same `memory_id` C21 · same keyed slot C21 | - |
| AC 21 search examples (8) | cosine order C23 · stale C24 · other user C24 · other workspace C24 · candidate C24 · area/type C24 · `k` C24 · boosts C24 | - |
| row tables (3) | memory C18 · runs C18 · meta C18 | - |
| startup config: backend selection (2 assemblies) | `Memory.__init__` C13 C15 · `memhub` CLI (Postgres URL) C9 | - |

## Swept

- validation: C12, C13 (a config asking for a capability the backend lacks)
- failure modes: C3, C26 (rollback per unit of work and per segment)
- idempotency: C25 (watermark and run rows the same as Postgres); existing - `test_ingest_e2e.py` re-runs on Postgres
- authorization: existing - `MemoryService` and `Memory._visible` are unchanged and run on both backends; C15 exercises promote/approve as admin
- concurrency: C20, C22
- data lifecycle: C15 (`delete_all`), C17, C19; `memory` rows are gone with the process by design (Out of scope)
- dependency failure: C15 (no Postgres reachable)
- state transitions: C21; existing - `test_store.py` lifecycle tests on Postgres; contract suite runs approve / reject / archive / supersede on memory
- observability: n/a - no logging changes; the bench script result goes in the PR description (AD-014)

## Handoff

- **Settled mid-build:** user chose "Own worktree": PR 3 builds in `~/worktrees/memhub-storage`; the PR 1 fix `2a58e6c` is its base, so C7, C10, C11 compare against `2a58e6c` (the last commit before the ports) instead of `fdec080`
- **Dispatched:** none yet
- **Boundary:** C1-C28 closed at `21e2f48` (refactor `b8f70ae`, backend and suites `21e2f48`); full suite on Postgres 602 passed before the new tests; N4 bench: ratio 0.954 and 0.998 (interleaved, 5 rounds)
- **Abandoned:** a bench that ran base then tree once (ratios 1.19 / 1.12 / 0.97: the second side always met a fuller database); replaced by interleaved rounds
