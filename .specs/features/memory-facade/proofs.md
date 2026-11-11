# memory-facade proofs

**HEAD**: fdec080
**Tree**: clean
**Result**: 56 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_defaults_without_config_touch_nothing` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_creates_columns_and_probe_dims` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_is_idempotent` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_init_upgrades_previous_ledger` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_call_before_init_names_init` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_add_then_search_through_each_db_kind` | 4 | 4 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_sqlite_is_not_implemented_yet` | 2 | 2 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_unknown_db_kind_is_type_error` | 3 | 3 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_config_behaviour_used_db_and_models_ignored` | 3 | 3 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_judge_defaults_to_llm` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_other_embedder_is_embedding_mismatch` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_pool_open_after_every_call` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_connection_open_after_every_call` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_failure_rolls_back_only_own_savepoint` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_callable_connections_closed` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_prepare_once_per_connection` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_pool_thread_lock_released` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_connection_thread_lock_released` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_missing_ids_raise_before_any_work` | 135 | 135 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_agent_id_is_optional` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_scope_binds_ids` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_scope_requires_ids` | 3 | 3 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_rows_carry_run_and_agent_id` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_get_and_history_hide_other_owners` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_review_needs_approving_role` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_user_scope_is_add_event` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_workspace_event_by_role` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_text_is_fact` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_infer_true_not_implemented` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_search_own_and_workspace_rows_only` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_get_all_by_status` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_update_returns_update_event` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_delete_archives_and_hard_deletes` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_delete_all_erases_only_caller` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_queue_workspace_candidates_conflicts_first` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_approve_reject_accept_memory_or_version_id` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_promote_returns_pending_workspace_event` | 1 | 1 | pass |
| C35 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_middleware_and_tools_share_service` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_toolkit_propose_returns_id_event_status` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest tests/test_middleware.py::test_propose_memory_tool_returns_id_event_status` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_ingest_runs_single_source` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_ingest_without_source_raises` | 1 | 1 | pass |
| C38 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_memory_event_fields` | 1 | 1 | pass |
| C39 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_no_embedding_in_any_return` | 1 | 1 | pass |
| C40 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_invalid_calls_raise` | 1 | 1 | pass |
| C41 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_result_dumps_to_json` | 1 | 1 | pass |
| C42 | `.venv/bin/python -m pytest tests` | 598 | 598 | pass |
| C43 | `.venv/bin/python -m pytest tests/test_connection_source.py::test_url_opens_and_closes_per_transaction` | 1 | 1 | pass |
| C44 | `.venv/bin/python -m pytest tests/test_cli.py::test_cli_without_database_url_names_key` | 3 | 3 | pass |
| C45 | `.venv/bin/python -m pytest tests/test_packaging.py::test_public_exports` | 1 | 1 | pass |
| C46 | `.venv/bin/python -m pytest tests/test_packaging.py::test_pyproject_dependencies` | 1 | 1 | pass |
| C47 | `.venv/bin/python -m pytest tests/test_packaging.py::test_azure_recipe` | 1 | 1 | pass |
| C47 | `.venv/bin/python -m pytest tests/test_packaging.py::test_core_imports_no_azure` | 1 | 1 | pass |
| C48 | `.venv/bin/python -m pytest tests/e2e/test_pr1_memory.py::test_pr1_demo` | 1 | 1 | pass |
| C48 | `.venv/bin/python -m pytest tests/e2e/test_pr1_memory.py::test_call_without_user_id_fails` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
