# sqlite-backend proofs

**HEAD**: da95bfb
**Tree**: clean
**Result**: 35 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_db_kinds_build_a_sqlite_backend_and_run_no_sql` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_sqlite_url_forms` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_borrowed_connection_stays_open_and_usable` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_second_writer_waits_for_the_first` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_commit_is_visible_and_an_exception_rolls_back` | 2 | 2 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_schema_columns_match_the_reference_and_init_is_i` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_rows_have_the_reference_keys_and_types` | 2 | 2 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_row_values_round_trip` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_one_active_row_per_memory_and_per_keyed_slot` | 2 | 2 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_link_lookups_use_json_each` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_areas_links_and_moves` | 2 | 2 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_capabilities` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_both_vector_paths_agree` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_search_filters_stale_area_type_and_k` | 2 | 2 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_search_boosts_area_text_and_entity_capped_at_on` | 2 | 2 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_vector_path_selection` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_doctor_reports_the_vector_path` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_forced_vec_without_the_extension_raises` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_reembed_to_new_dims` | 2 | 2 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_a_vector_of_the_wrong_size_is_refused` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_contract_suite_runs_on_both_vector_paths_with_no` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_sqlite_docs` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_thread_lock_names_the_capability_and_the_fix` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_database_schema_is_refused_at_startup` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_schemas_capability` | 2 | 2 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_ingest_locks_only_with_parallel_ingest` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_a_call_before_init_names_init` | 2 | 2 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_memory_on_sqlite_needs_init_and_the_same_dims` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/e2e/test_pr4_sqlite_ingest.py::test_init_ingest_approve_search_on_sqlite_with` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_sqlite_backend.py::test_ingest_matches_the_in_memory_backend` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_contract_sqlite.py::test_failed_segment_leaves_nothing_and_is_retried` | 2 | 2 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_sqlite_vec_is_an_extra_not_a_core_dependency` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_sqlite_packaging.py::test_wheel_contains_the_sqlite_schema` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest -q tests` | 837 | 837 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
