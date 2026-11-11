# Schema version and migrations - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/schema-migrations/plan.md`

17 checks in 4 slices · 4 one-way doors · 0 open

## Checks

### S1 - A ledger knows its version

**C1** - After `init()`, `schema_version()` is `2` and meta `schema_version` holds `2` on Postgres, SQLite and the in-memory backend; before `init()` it is `None` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_init_records_the_version`

**C2** - A ledger with tables and no `schema_version` row is version `1`: the v0.1.0 Postgres fixture, and a SQLite file whose row was deleted (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_no_version_row_is_version_one`

**C3** - `pending(b, 1)` is `["0002_run_and_agent_ids"]` and `pending(b, 2)` is `[]` for `postgres`, `sqlite`, `memory`; `pending(b, None)` raises; `postgres/` and `sqlite/` each hold exactly versions `2..SCHEMA_VERSION`, one file each, named `NNNN_name.sql` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_pending_and_the_files`
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_wheel_ships_the_migrations`

### S2 - An old ledger is refused, then upgraded

**C4** - On a version-1 ledger, `add`, `search`, `get_all` and `queue` each raise `SchemaOutdated` with exactly ``the ledger is at schema version 1 and this memhub needs 2: run `memhub migrate` `` and the ledger is unchanged afterwards (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_memory_refuses_an_old_ledger`

**C5** - On a version-3 ledger every call and `migrate()` raise `SchemaOutdated` with exactly `the ledger is at schema version 3, newer than this memhub (2): upgrade memhub`, and the version stays 3 (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_a_newer_ledger_is_refused`

**C6** - `migrate()` on the v0.1.0 Postgres fixture adds nullable `run_id` and `agent_id`, keeps the old row's values with both new columns `NULL`, sets the version to 2 and returns `["0002_run_and_agent_ids"]` (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_a_v0_1_0_postgres_ledger`

**C7** - `migrate()` on a current ledger returns `[]`; twice in a row the second returns `[]` - on each backend (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_is_idempotent`

**C8** - A migration whose second statement fails leaves its first statement's change undone and the version at 1, on Postgres and SQLite (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_a_failed_migration_applies_nothing`

**C9** - Two `migrate()` calls started together on one Postgres ledger both return without error, and between them `0002_run_and_agent_ids` is reported once (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_two_migrations_at_once_apply_once`

**C10** - `init()` on a version-1 ledger leaves it at version 2 with `run_id` / `agent_id` present (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_init_upgrades_an_old_ledger`
Proof: `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_upgrades_previous_ledger`

**C11** - `migrate()` where no ledger exists raises `EmbeddingMismatch` naming `init()` and creates no table (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_without_a_ledger`

### S3 - The CLI

**C12** - `memhub migrate` on a version-1 ledger prints `applied 0002_run_and_agent_ids` and `schema version 1 -> 2`, exit 0; again: `already at schema version 2`, exit 0 (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate`

**C13** - `memhub migrate --dry-run` on a version-1 ledger prints `pending 0002_run_and_agent_ids`, exit 0, and the version stays 1 (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate_dry_run`

**C14** - `memhub migrate` exits 1 with the reason when there is no ledger and when the ledger is version 3 (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate_failures`

**C15** - On a version-1 ledger `memhub search`, `add`, `queue` and `list` exit 1 and print the `SchemaOutdated` message naming `memhub migrate` (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_commands_refuse_an_old_ledger`

**C16** - `memhub doctor` prints `ok  version  schema version 2` on a current ledger; on a version-1 ledger a `fail  version` line naming `memhub migrate`, exit 1 (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_doctor_reports_the_version`

### S4 - The demo

**C17** - A Postgres database created from `tests/data/v0_1_0_ledger.sql` is refused by `Memory.search` with `SchemaOutdated`; after `memhub migrate`, `Memory.add` with a `run_id` stores it and `Memory.search` finds the v0.1.0 row; the docs describe `memhub migrate` and the refusal; the full suite stays green (AC 17, AC 18)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr6_migrate.py::test_v0_1_0_ledger_refused_migrated_then_used`
Proof: `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_docs`
Proof: `.venv/bin/python -m pytest -q tests`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (4) | backend port C1 C7 C11 · files and numbering C3 · where the version lives C1 C2 · the refusal C4 C5 | - |
| backends (3) x version read and write | Postgres C1 C2 C6 · SQLite C1 C2 C8 · in-memory C1 C7 | - |
| AC 2 examples (2) | v0.1.0 Postgres C2 · SQLite with the row deleted C2 | - |
| AC 3 examples (3) | current 1 C3 · current 2 C3 · current None C3 | - |
| `Memory` methods refused (4) | `add` C4 · `search` C4 · `get_all` C4 · `queue` C4 | - |
| `memhub migrate` exits (2) | `0` C12 C13 · `1` C14 | - |
| CLI commands refused (4) | `search` C15 · `add` C15 · `queue` C15 · `list` C15 | - |
| version states (4) | none C11 · older C4 C6 · current C7 · newer C5 C14 | - |
| startup config: version checks (2 assemblies) | `Memory._ready` C4 · CLI `_service` C15 | - |

## Swept

- validation: C3 (file names and numbering), C5 (a version newer than the code)
- failure modes: C8 (one transaction per run)
- idempotency: C7, C10 (`init` twice)
- authorization: n/a - whoever holds the database credentials may migrate; memhub roles do not apply to the schema (plan Assumptions)
- concurrency: C9 (advisory lock on Postgres); SQLite's `BEGIN IMMEDIATE` serialises writers (PR 4 C4)
- data lifecycle: C6 (old rows keep their values), C17 (the v0.1.0 row found after migrating)
- dependency failure: C11 (no ledger), C14
- state transitions: C1, C4, C5, C6, C7 (none -> current, older -> current, current -> current, newer refused)
- observability: C12, C13, C16 (what was applied, what is pending, doctor's version line)

## Handoff

- **Settled mid-build:** user: "Any question you need from me, I'll let you decide the best decision" - version 1 is the v0.1.0 schema (no row = 1); `init()` keeps upgrading (AD-002); doctor gains a `version` line, so PR 2's list of checks gains it (AD-009)
- **Dispatched:** Verifier at `c6c206e`, diff `51ef4e5..c6c206e` (no screens sub-agent: no capture)
- **Boundary:** C1-C17 closed at `c6c206e`
- **Abandoned:** none
- **Walked:** n/a - no screen
