# schema-migrations proofs

**HEAD**: 422d645
**Tree**: clean
**Result**: 22 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_migrations.py::test_init_records_the_version` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_migrations.py::test_no_version_row_is_version_one` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_migrations.py::test_pending_and_the_files` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_migrations.py::test_wheel_ships_the_migrations` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_migrations.py::test_memory_refuses_an_old_ledger` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_migrations.py::test_a_newer_ledger_is_refused` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_a_v0_1_0_postgres_ledger` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_is_idempotent` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_migrations.py::test_a_failed_migration_applies_nothing` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_migrations.py::test_two_migrations_at_once_apply_once` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_migrations.py::test_init_upgrades_an_old_ledger` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_upgrades_previous_ledger` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_without_a_ledger` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate_dry_run` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_migrate_failures` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_migrations.py::test_cli_commands_refuse_an_old_ledger` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_migrations.py::test_doctor_reports_the_version` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/e2e/test_pr6_migrate.py::test_v0_1_0_ledger_refused_migrated_then_used` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_migrations.py::test_migrate_docs` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest -q tests` | 856 | 856 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
