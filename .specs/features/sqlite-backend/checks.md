# SQLite backend - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/sqlite-backend/plan.md`

24 checks in 4 slices · 5 one-way doors · 0 open

## Checks

### S1 - SQLite behind the same ports

**C1** - `open_backend` gives a `SqliteBackend` for a `sqlite3.Connection`, `sqlite:///m.db`, `sqlite:////abs/m.db`, `sqlite:///:memory:` and a callable returning a `sqlite3.Connection`, and creates no table before `init()` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_db_kinds_build_a_sqlite_backend_and_run_no_sql`

**C2** - `sqlite:///m.db` is the relative file `m.db`, `sqlite:////tmp/x/m.db` the absolute one; `sqlite://`, `sqlite:m.db`, `sqlite:///`, `sqlite:////` and `sqlite:///  ` raise `TypeError` naming `sqlite:///path` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_sqlite_url_forms`

**C3** - A borrowed `sqlite3.Connection` answers `select 1` after `m.add(...)` and after a failing `m.add(...)`, keeps its `row_factory`, and an open app transaction is neither committed nor rolled back by memhub (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_borrowed_connection_stays_open_and_usable`

**C4** - Each unit of work starts with `BEGIN IMMEDIATE`: with two backends on one file, the second writer waits for the first's commit and then sees its row, with no `database is locked` error (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_second_writer_waits_for_the_first`

**C5** - A clean unit of work is visible to the next; one that raises leaves no memory row and no run row and re-raises (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_commit_is_visible_and_an_exception_rolls_back`

**C6** - `init()` creates `{prefix}_memory`, `{prefix}_memory_runs`, `{prefix}_memhub_meta` whose column names are exactly `MEMORY_COLUMNS` / `RUN_COLUMNS` / `key, value`; a second `init()` changes nothing (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_schema_columns_match_the_reference_and_init_is_idempotent`

**C7** - Rows carry exactly the reference keys; `id` / `memory_id` / `conflicts_with` are `uuid.UUID`, timestamps aware `datetime` equal to what was written and stored as UTC text (a +02:00 `valid_until` and a -05:00 `now` an hour apart compare by time), `payload` / `entities` / `evidence` / `links` Python dicts and lists, `verified` a `bool`, `embedding` a `list[float]` within 1e-6 of what was written (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_rows_have_the_reference_keys_and_types`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_row_values_round_trip`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_stale_filter_compares_times_not_offsets`

**C8** - A second active row for one `memory_id`, or for one keyed slot, raises `sqlite3.IntegrityError` and one active row stays (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_one_active_row_per_memory_and_per_keyed_slot`

**C9** - `linked_rows` and `move_area_links` run `json_each` over `links`; a row linking areas A and B is found for A and for B and not for C; `add_link` of an existing link leaves one copy (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_link_lookups_use_json_each`
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_areas_links_and_moves`

**C10** - `SqliteBackend.capabilities == {transactions, vector_search, json_lookup}` and `name == "sqlite"` (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_capabilities`

### S2 - Vector search with and without sqlite-vec

**C11** - On the same data, the sqlite-vec path and the Python path return the same rows in the same order from `search`, `top_similar` and `nearest_area`, with `similarity` 0.95 / 0.86 / 0.50 within 1e-6; a stale 0.99 row only with `include_stale`; another user's 0.99 row never (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_both_vector_paths_agree`

**C12** - Search filters, `k` and the +0.05 boosts capped at 1.0 hold on both vector paths, including for a linked row ranked ninth on similarity with `k=3`, which comes back first (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_search_filters_stale_area_type_and_k`
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_search_boosts_area_text_and_entity_capped_at_one`
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_search_boosts_rows_ranked_below_k`

**C13** - `vector_path` is `python` when `sqlite_vec` cannot be imported, when the connection cannot load extensions, and when `MEMHUB_SQLITE_VEC=0`; it is `sqlite-vec` otherwise; on the fallback `search` returns the 0.95 row first (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_vector_path_selection`

**C14** - `memhub doctor` on a SQLite config prints the vector path (`sqlite-vec` or `python`) (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_doctor_reports_the_vector_path`

**C15** - With `MEMHUB_SQLITE_VEC=1` and no loadable sqlite-vec, building the backend raises `NotImplementedError` starting `vector_search is not available on sqlite: install memhub[sqlite]` (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_forced_vec_without_the_extension_raises`

**C16** - After `set_embedding_dims(20)` and `reembed`, every row (candidate and superseded included) has a 20-dim vector and meta `embedding` holds the new model and 20, on both paths; a write with a 15-dim vector on a 16-dim ledger raises `StoreError` naming 16 and 15 (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_reembed_to_new_dims`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_a_vector_of_the_wrong_size_is_refused`

**C17** - `tests/test_contract_sqlite.py` runs every contract test once per vector path (`sqlite-vec`, `python`): 2 x the contract's tests pass and 0 are skipped (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_contract_suite_runs_on_both_vector_paths_with_no_skip`

**C18** - `docs/sqlite.md` states the fallback's limit of about 50k rows per owner and that without `memhub[sqlite]` the fallback is used; the README links it (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_sqlite_docs`

### S3 - What SQLite cannot do fails loudly

**C19** - `backend.thread_lock(...)` on SQLite raises `NotImplementedError("parallel_ingest is not available on sqlite: run one ingest worker or use Postgres")`, exactly (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_thread_lock_names_the_capability_and_the_fix`

**C20** - `Memory(db=<sqlite>, config={"database_schema": "tenant"})` raises `NotImplementedError("schemas is not available on sqlite: remove database_schema from the config, or use Postgres")` in `__init__` (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_database_schema_is_refused_at_startup`
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_schemas_capability`

**C21** - `ingest_source` on a backend without `parallel_ingest` ingests every thread, never calls `thread_lock`, and `threads_locked` is 0; on a backend with it, `thread_lock` is called once per thread and a thread whose lock is taken is counted in `threads_locked` (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_ingest_locks_only_with_parallel_ingest`

**C22** - A `Memory` call on SQLite before `init()` raises `EmbeddingMismatch` naming `init()`; a `Memory` whose embedder has 8 dims on a 16-dim ledger raises `EmbeddingMismatch` (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_a_call_before_init_names_init`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_memory_on_sqlite_needs_init_and_the_same_dims`

### S4 - A full local run, no Docker

**C23** - `memhub init --db sqlite:///m.db`, `memhub ingest` of a JSONL log, `memhub approve` of the workspace candidate and `memhub search` all exit 0 with `psycopg.connect` patched to raise, and the search prints the approved memory first (AC 18)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr4_sqlite_ingest.py::test_init_ingest_approve_search_on_sqlite_without_postgres`

**C24** - The same JSONL fixture and fake models write the same memory rows (type, scope, status, content) and run rows (thread, `candidates_proposed`, `created`, dropped reasons) on SQLite as in memory; a failed segment leaves nothing and is retried; the whole suite stays green; `sqlite-vec` is in the `sqlite` and `dev` extras and not in the core dependencies (AC 19, AC 20)
Proof: `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_ingest_matches_the_in_memory_backend`
Proof: `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_failed_segment_leaves_nothing_and_is_retried`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_sqlite_vec_is_an_extra_not_a_core_dependency`
Proof: `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_wheel_contains_the_sqlite_schema`
Proof: `.venv/bin/python -m pytest -q tests`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (5) | `db` values and lifecycle C1 C2 C3 C4 · backend and capabilities C10 C19 C20 · DDL and storage types C6 C7 C8 · vector path C11 C13 C15 C16 · dependency C24 | - |
| accepted `db` kinds (5) | `sqlite3.Connection` C1 C3 · relative URL C1 C2 · absolute URL C1 C2 · memory URL C1 · callable C1 | - |
| AC 1 URL examples (3) | `sqlite:///m.db` C2 · `sqlite:////tmp/m.db` C2 · `sqlite://` C2 | - |
| AC 2 examples (3) | after `add` C3 · after a failing `add` C3 · two writers C4 | - |
| unit-of-work outcomes (2) | clean exit C5 · exception C5 C24 | - |
| tables (3) | memory C6 C7 · runs C6 C7 · meta C6 | - |
| AC 5 uniqueness (2) | same `memory_id` C8 · same keyed slot C8 | - |
| AC 6 examples (4) | found for A C9 · found for B C9 · not for C C9 · `add_link` twice C9 | - |
| capabilities (5) on SQLite | `transactions` C5 · `vector_search` C11 · `json_lookup` C9 · `parallel_ingest` absent C19 C21 · `schemas` absent C20 | - |
| contract suite legs (2) | `sqlite-vec` C17 · `python` C17 - table-driven over all 24 contract tests per leg | - |
| AC 8 examples (3) | 0.95 / 0.86 / 0.50 order C11 · stale 0.99 C11 · other user's 0.99 C11 | - |
| vector path selection (4) | package missing C13 · extension loading refused C13 · `MEMHUB_SQLITE_VEC=0` C13 · `=1` without the extension C15 | - |
| vector readers (3) x paths (2) | `search` C11 · `top_similar` C11 · `nearest_area` C11 | - |
| AC 16 backends (2) | without `parallel_ingest` C21 · with it C21 | - |
| AC 17 examples (2) | before `init()` C22 · other dims C22 | - |
| demo steps (4) | init C23 · ingest C23 · approve C23 · search C23 | - |
| startup config: backend selection (2 assemblies) | `Memory.__init__` C1 C20 · `memhub` CLI (`database_url: sqlite:///`) C23 | - |

## Swept

- validation: C2 (URL shapes), C16 (vector size), C20 (a config asking for schemas)
- failure modes: C5, C24 (rollback per unit of work and per segment); C3 (a failing call leaves the app's connection usable)
- idempotency: C6 (`init()` twice), C9 (`add_link` twice); existing - evidence dedup in the contract suite (`test_merge_evidence_dedups_counts_threads_and_merges_dates`, counted in C17)
- authorization: existing - `MemoryService` and `Memory._visible` are unchanged; C23 approves as `workspace_admin`
- concurrency: C4 (two writers on one file); several ingest workers are refused, C19
- data lifecycle: C7 (what was written comes back unchanged), C16 (re-embed); `delete_user` in the contract suite (C17)
- dependency failure: C13 (sqlite-vec missing or not loadable), C15 (forced and missing), C23 (no Postgres reachable)
- state transitions: existing - approve / reject / archive / supersede / conflict resolutions in the contract suite on both paths (C17)
- observability: C14 (`doctor` names the vector path)

## Handoff

- **Settled mid-build:** user: "Any question you need from me, I'll let you decide the best decision, manage all problems." - door 4 uses `vec_distance_cosine` over the BLOB column, no `vec0` table; a callable `db` is detected on first use (`DeferredBackend`, AD-002); PR 3's guard pinned to PR 3's diff (AD-021); `FIXES[parallel_ingest]` takes the PRD's wording; minimum SQLite 3.38
- **Dispatched:** Verifier at `da95bfb`, diff `3db6080..da95bfb` (no screens sub-agent: no capture)
- **Boundary:** C1-C24 closed at `da95bfb`; `check_proofs.py` 35 pass, 0 fail; full suite 836 passed
- **Abandoned:** probing a callable `db` inside `open_backend` (opened a connection in `Memory(...)`, against AD-002)
- **Walked:** n/a - no screen
