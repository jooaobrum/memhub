# storage-ports proofs

**HEAD**: ab59ad0
**Tree**: clean
**Result**: 47 pass, 0 fail; mutations 3 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_capability_values_and_backends_satisfy_protocol` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_repository_methods_match_memorystore_signatures` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_commit_is_visible_and_an_exception_rolls_back` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_commit_is_visible_and_an_exception_rolls_back` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_postgres_capabilities_for_every_db_kind` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_bind_shares_the_cursor_transaction` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_no_psycopg_outside_the_postgres_backend` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_store_shim_exports_every_name_from_before_the_por` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_pure_helpers_import_without_psycopg` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest -q tests` | 680 | 680 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_old_test_files_change_only_at_pipeline_call_sites` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_search_sql.py::test_search_issues_the_sql_recorded_before_the_ports` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_missing_capability_message_is_exact` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_memory_init_checks_needed_capabilities` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_schemas_capability` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_memory_contract_skips_nothing` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_app_flow_on_memory_without_postgres` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_memory_urls_isolate_or_share_by_name` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_init_required_and_dims_checked` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_rows_have_the_reference_keys_and_types` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_rows_have_the_reference_keys_and_types` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_returned_rows_are_copies` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_units_of_work_serialize_across_threads` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_one_active_row_per_memory_and_per_keyed_slot` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_one_active_row_per_memory_and_per_keyed_slot` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_parallel_ingest_capability` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_parallel_ingest_capability` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_search_ranks_by_cosine_similarity` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_search_ranks_by_cosine_similarity` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_contract_memory.py -k "test_search_filters or test_search_boosts"` | 4 | 4 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "test_search_filters or test_search_boosts"` | 4 | 4 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_ingest_writes_the_expected_rows` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_ingest_writes_the_expected_rows` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_failed_segment_leaves_nothing_and_is_retried` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_failed_segment_leaves_nothing_and_is_retried` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_reembed_to_new_dims` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_reembed_to_new_dims` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_a_call_before_init_names_init` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_a_call_before_init_names_init` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_keyed_slot_compares_keys_as_json_text` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_keyed_slot_compares_keys_as_json_text` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/e2e/test_pr3_memory_backend.py::test_a_named_url_keeps_one_ledger_per_table_p` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_naive_datetimes_are_stored_as_utc` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_contract_postgres.py::test_naive_datetimes_are_stored_as_utc` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_contract_memory.py::test_a_unit_of_work_inside_another_in_one_thread_is_` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_storage_ports.py::test_contract_capability_tests_assert_the_error_on_a_b` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| C24: in-memory stale boundary valid_until < now -> <= (Postgres keeps `<`) | `src/memhub/storage/memory.py:146` | yes |
| C21: keyed-slot uniqueness ignores workspace_id | `src/memhub/storage/memory.py:132` | yes |
| C3: unit of work writes the live tables (no snapshot, no rollback) | `src/memhub/storage/memory.py:687` | yes |
