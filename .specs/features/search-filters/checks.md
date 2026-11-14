# Search filters - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/search-filters/plan.md`

36 checks in 4 slices · 3 one-way doors · 0 open

Run from the worktree with `PYTHONPATH=src` exported, so the tests import this tree's `src` and not the editable install's. The Postgres legs need Docker (the `pg_dsn` fixture).

## Checks

### S1 - one grammar, the same rows on every backend

**C1** - On every backend, `vectors.search(filter=<entities machine id prefix "PRD_">)` keeps `PRD_1`, `PRD_10`, `PRD_2`, `PRD_3` and drops `PRDX1`, `prd_1` and `LINE_1` (AC 1, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_entity_id_prefix"`

**C2** - `Memory.search` and `Memory.get_all` with `filters={"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}` return the memories of `PRD_1`, `PRD_2`, `PRD_3` and none of `LINE_1` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "prefix_through_search_and_get_all"`

**C3** - On every backend, `id: {"in": ["PRD_1", "PRD_2"]}` keeps `PRD_1` and `PRD_2` and drops `PRD_3`; `id: {"contains": "D_1"}` keeps `PRD_1`, `PRD_12` and drops `prd_1`, `PRD_2` (AC 2, AC 3, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_entity_id_in_and_contains"`

**C4** - On every backend, `AND` of `PRD_1` and `PRD_2` keeps only the memory naming both; `OR` of `PRD_1` and `entity_type line` keeps the `PRD_1` memory and the `line` memory, each once; `NOT` of `PRD_1` keeps every other visible memory, the untagged one included (AC 4, AC 5, AC 6, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_and_or_not"`

**C5** - On every backend, `entity_type: {"in": ["machine", "line"]}` keeps memories naming a machine or a line and drops one naming only a `part` (AC 7, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_entity_type_in"`

**C6** - On every backend, an area filter of area memory ids keeps the rows linked `in_area` to one of them, and an empty id set keeps no row (AC 8, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_areas"`

**C7** - Through `Memory.search` and `get_all`, `areas: "Bearings"` keeps memories in the caller's area keyed `bearings` (user area and workspace area alike); `{"in": ["bearings", "lubrication"]}` keeps both areas' memories; `"bearings"` against an area titled `Bearings & seals` keyed `seals` keeps none; `"nowhere"` returns `[]` with no error (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "areas_by_key_or_title"`

**C8** - On every backend, `observed_at {"gte": "2026-10-01", "lte": "2026-10-31"}` keeps a row observed 2026-10-31T15:00Z and 2026-10-01T00:00Z, and drops 2026-11-01T00:00Z and 2026-09-30T23:59Z; `{"gte": "2026-10-01T12:00:00+00:00"}` drops 2026-10-01T11:59Z (AC 9, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_observed_at_bounds"`

**C9** - On every backend, `valid_until {"gte": "2026-12-01"}` keeps rows expiring 2026-12-01T10:00Z and never (null) and drops 2026-11-30; `{"lte": "2026-12-01"}` keeps 2026-11-30 and 2026-12-01T10:00Z and drops the null row (AC 10, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_valid_until_bounds"`

**C10** - On every backend, `content {"contains": "e47"}` keeps `Error E47 on start`; `"ölpumpe"` keeps `Ölpumpe defekt`; `"50%"` drops `500 rpm` (AC 11, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_content_contains"`

**C11** - On every backend, `NOT valid_until {"lte": "2026-12-01"}` keeps the row whose `valid_until` is null, and `NOT observed_at {"gte": ...}` keeps a row observed before the bound (AC 12, AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_not_of_null_comparison"`

**C12** - `Filter.matches(row)` (the reference `service.list` uses) agrees with every backend's `vectors.search` on the same rows for each node kind, table-driven over 12 filters (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_matches_agrees_with_search"`

**C13** - On every backend, 30 nearer memories naming `LINE_1` and 2 naming `PRD_1`, `k=5`, prefix `PRD_` → both `PRD_1` memories returned (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "filter_fills_k_from_matching_rows"`

**C14** - Caller `alice` with `{"NOT": {"entities": [LINE_1]}}`, a prefix `PRD_`, and `areas` naming `bob`'s user area gets none of `bob`'s user memories nor workspace `w2`'s memories, through `search` and `get_all` (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "never_returns_another_owners_rows"`

### S2 - today's filters keep their result, and the grammar fails early

**C15** - The subject tests written before #25 pass unchanged (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_subjects.py tests/e2e/test_subjects_demo.py`
Proof: `git diff --exit-code 65233c5 -- tests/test_subjects.py tests/e2e/test_subjects_demo.py`

**C16** - `search(filters={"entities": [A], "entity_type": "line"})` returns only the memory naming `A` and a line, not the one naming `A` alone; `get_all` returns the same set (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "entities_and_entity_type_are_anded"`

**C17** - Postgres `service.search` with exact-id tiers, `widen`, `entity_type` and the guard issues the SQL recorded before #25, and with no filter the SQL recorded before the ports (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_search_sql.py`

**C18** - Each of 14 invalid filters raises `ValidationError` naming the key or value, through `search` and `get_all`, with the backend and the embedder never called: `{"tags": "x"}`, `{"areas": {"prefix": "b"}}`, `{"types": {"in": ["fact"]}}`, `{"observed_at": {"gte": "last month"}}`, `{"OR": []}`, `{"AND": {}}`, `{"NOT": [..]}`, `{"entity_type": {"in": []}}`, `{"entities": {"type": "machine", "id": {"prefix": ""}}}`, `{"content": {"contains": ""}}`, two operators on an id, `{"entities": {"type": "robot", "id": "x"}}`, `{"observed_at": {"after": "2026-01-01"}}`, a non-dict filter (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "invalid_filter_raises_before_any_call"`

**C19** - `types=["fact"]` with `filters={"types": ["fact"]}` raises `ValidationError` (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "types_given_twice"`

### S3 - tiers, widen and the guard with the new grammar

**C20** - `{"entities": [{"type": "machine", "id": {"prefix": "PRD_"}}, LINE_1], "areas": "bearings"}` returns the `PRD_*` memories in bearings, then the `LINE_1` memory in bearings, and none outside bearings (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "tiers_follow_the_entities_order"`

**C21** - A row naming `[line:L1, machine:PRD_2]` hit through prefix `PRD_` has `matched_on == "machine:PRD_2"`; through `{"AND": [{"entities": [line L1]}, {"entity_type": "machine"}]}` it has `"line:L1"` (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "matched_on_names_the_entity_that_matched"`

**C22** - `filters={"content": {"contains": "pump"}}` hits carry no `matched_on` (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "no_entity_predicate_no_matched_on"`

**C23** - `{"entities": [PRD_1], "content": {"contains": "pump"}}, widen=True` returns the `PRD_1` pump memory with `matched_on` `machine:PRD_1`, then a general pump memory with `general`, and not a general fan memory (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "widen_keeps_the_other_top_level_keys"`

**C24** - `widen=True` with `{"areas": "x"}` and with `{"AND": [{"entities": [PRD_1]}]}` raises `ValidationError` before any call (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "widen_needs_top_level_entities"`

**C25** - With `patient` `require_filter`, `{"NOT": {"entities": [patient p1]}}` returns no patient memory; `{"OR": [{"entities": [patient p1]}, {"types": ["fact"]}]}` returns p1's memory; `{"entity_type": {"in": ["patient"]}}` returns patient memories (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "guard_counts_positive_branches_only"`

### S4 - the CLI, the tools and MCP take the same filter

**C26** - `memhub search "vibration" --filter '{"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}'` exits 0 and prints as JSON the same memory ids `Memory.search` returns for that filter (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_search_filter"`

**C27** - `memhub list --entity machine:PRD_1 --entity machine:PRD_2` lists exactly the memories naming either; `memhub search` with the same flags lists the `PRD_1` hit before the `PRD_2` hit (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_entity_flags"`

**C28** - `--filter '{bad'`, `--filter '{"tags": 1}'`, `--entity machinePRD_1`, and `--entity machine:PRD_1 --filter '{"entities": [...]}'` each exit 1 with the reason on stderr and nothing on stdout, for `search` and `list` (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_bad_filter_exits_1"`

**C29** - `search_memory` with `filters` as an object and as JSON text returns the `PRD_*` hits through `MemoryToolkit.call`, the middleware tool and the MCP server (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "tools_take_filters"`

**C30** - An invalid `filters` makes the toolkit and MCP tool return `{"error": ...}` naming the key, and the middleware tool `invalid filters: ...`, with no search run (AC 31)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "tools_report_bad_filters"`

**C31** - With no filter, the toolkit, middleware, MCP server and `memhub search` return no memory tagged with a `require_filter` type (AC 32)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "guard_holds_without_filter_everywhere"`

**C32** - `MemoryToolkit.tool_specs()`, the MCP `search_memory` schema and the middleware tool's args declare `filters` optional (not in `required`), with a description naming `entities`, `areas`, `AND` and an example (AC 33)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "tool_schemas_declare_filters"`
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py -k "tools_and_their_schemas"`

**C33** - `docs/subjects.md` no longer holds "take no filter yet", and the filter reference names every key (`entities`, `entity_type`, `types`, `areas`, `observed_at`, `valid_until`, `content`) and operator (`in`, `prefix`, `contains`, `gte`, `lte`, `AND`, `OR`, `NOT`) (AC 34)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "docs_reference_the_grammar"`

### Added

**C34** - SQLite runs the content filter with Python's `str.lower` on a borrowed `sqlite3.Connection`, and the connection stays open and usable afterwards (AC 11; AD-004)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "sqlite_borrowed_connection_content_filter"`

**C35** - A time bound with no timezone is read as UTC: `{"gte": "2026-10-01T12:00:00"}` keeps 12:00Z and drops 11:59Z (assumption: naive = UTC)
Proof: `.venv/bin/python -m pytest tests/test_search_filters.py -k "naive_datetime_is_utc"`

**C36** - The full suite stays green (N1)
Proof: `.venv/bin/python -m pytest -q`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| backends (4) | Postgres C1 · SQLite with sqlite-vec C1 · SQLite fallback C1 · in-memory C1 (same for C3-C13, the contract files run every leg) | - |
| grammar fields (7) | `entities` C1 C3 C4 · `entity_type` C4 C5 · `types` C12 C19 · `areas` C6 C7 · `observed_at` C8 · `valid_until` C9 · `content` C10 | - |
| operators and combinators (8) | `in` C3 C5 C7 · `prefix` C1 · `contains` C3 C10 · `gte` C8 C9 · `lte` C8 C9 · `AND` C4 · `OR` C4 · `NOT` C4 C11 | - |
| prefix rule, AC 1 (4 examples) | `PRD_1` C1 · `PRD_10` C1 · `PRDX1` C1 · `prd_1` C1 | - |
| contains on id, AC 3 (3 examples) | `PRD_1` C3 · `PRD_12` C3 · `prd_1` C3 | - |
| area rule, AC 8 (3 examples) | key `bearings` vs `"Bearings"` C7 · title `Bearings & seals` C7 · no such area C7 | - |
| observed_at rule, AC 9 (4 examples) | 10-31T15:00 C8 · 11-01T00:00 C8 · 09-30T23:59 C8 · datetime bound 11:59 C8 | - |
| content rule, AC 11 (3 examples) | `E47` C10 · `Ölpumpe` C10 · `50%` C10 | - |
| invalid filter kinds, AC 19 (14) | C18, table-driven over all 14 | - |
| `Memory.search` statuses (3) | 200 C2 · 422 C18 · 404 C14 | - |
| `Memory.get_all` statuses (3) | 200 C2 · 422 C18 · 404 C14 | - |
| `memhub search` statuses (3) | 200 C26 · 422 C28 · 404 C14 (same service path) | - |
| `memhub list` statuses (3) | 200 C27 · 422 C28 · 404 C14 (same service path) | - |
| tool `search_memory` statuses (3) x 3 tools | 200 toolkit/middleware/MCP C29 · 422 toolkit/middleware/MCP C30 · 404 C14 (same service path) | - |
| tool entry points (3) | toolkit C29 C30 C31 C32 · middleware C29 C30 C31 C32 · MCP C29 C30 C31 C32 | - |
| Landing doors (3) | 1 filter in the query C13 · 2 grammar contract C2 C15 C18 · 3 tool schemas C32 | - |
| decisions (5) | AD-047 C23 C24 · AD-048 C29 · AD-049 C8 · AD-050 C9 · AD-051 C16 | - |

## Swept

- validation: C18, C19, C24, C28, C30
- failure modes: C30 (a bad filter from a model never raises); read-only, nothing to roll back
- idempotency: n/a - read only
- authorization: C14; ids required on every call stays as AD-001 (existing - `test_memory_api.py`)
- concurrency: n/a - read only, one query per tier inside one unit of work
- data lifecycle: n/a - nothing written; stale rows stay out of search as today (existing - `test_search_filters_stale_*` contract)
- dependency failure: existing - the embedder and database errors surface as today; a bad filter raises before either is called (C18)
- state transitions: n/a - no state changes
- observability: n/a - no logging requirement; `matched_on` (C21-C23) shows why a hit came back

## Handoff

- **Settled mid-build:** none
- **Dispatched:** none yet
- **Boundary:** none yet
- **Abandoned:** none
