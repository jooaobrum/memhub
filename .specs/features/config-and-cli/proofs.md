# config-and-cli proofs

**HEAD**: fa9a5ac
**Tree**: dirty - .claude/
**Result**: 63 pass, 0 fail; mutations 5 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_settings.py::test_layers_last_wins_over_each_lower_layer` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_settings.py::test_blocks_merge_per_key_scalars_and_lists_replace` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_settings.py::test_discovery_takes_first_existing_place` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_settings.py::test_no_yaml_anywhere_gives_defaults` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_settings.py::test_memhub_config_env_pointing_nowhere_is_error` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_settings.py::test_config_path_dict_and_object_skip_discovery` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_settings.py::test_env_keys_map_to_nested_yaml_values` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_settings.py::test_reserved_and_unknown_env_vars_are_not_config` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_settings.py::test_env_var_on_types_is_error` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_settings.py::test_project_and_db_aliases` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_settings.py::test_examples_still_load` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_settings.py::test_optional_keys_may_be_absent` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_settings.py::test_preset_layer_and_unknown_preset` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_settings.py::test_types_block_replaces_adds_removes` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_presets.py::test_presets_hold_no_project_keys_and_build_registry` | 3 | 3 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_presets.py::test_each_preset_defines_its_types_and_policy` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_presets.py::test_presets_are_package_resources` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_packaging.py::test_wheel_contains_presets` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_connect.py::test_connect_factory_called_once_with_no_args_and_objects_us` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_connect.py::test_connect_misuse_is_config_error` | 5 | 5 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_connect.py::test_models_strings_build_through_langchain_inits` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_connect.py::test_legacy_blocks_win_over_models` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_connect.py::test_missing_db_or_embedder_source_errors` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_connect.py::test_objects_passed_beat_connect_and_skip_it` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_connect.py::test_memory_without_arguments_works_end_to_end` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_connect.py::test_memory_without_arguments_and_no_yaml_lists_places` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_connect.py::test_connect_db_left_open` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_connect.py::test_memory_accepts_overrides` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_flags_writes_yaml_and_tables` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_accepts_literal_url_password` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_connect_excludes_db_and_models` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_chat_flag` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_prompts_for_missing_values` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_order_probe_before_tables_before_yaml` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_failure_writes_no_yaml` | 1 | 1 | pass |
| C35 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_rejects_bad_preset_or_project` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_existing_yaml_needs_force` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_c_or_no_flags_keeps_old_behavior` | 1 | 1 | pass |
| C38 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_lines_and_sources` | 1 | 1 | pass |
| C39 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_masks_secrets` | 1 | 1 | pass |
| C40 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_writes_validated_value_keeps_refs` | 1 | 1 | pass |
| C41 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_invalid_or_missing_yaml` | 1 | 1 | pass |
| C42 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_set_refuses_literal_secret` | 4 | 4 | pass |
| C43 | `.venv/bin/python -m pytest tests/test_settings.py::test_save_round_trips_and_keeps_var_refs` | 1 | 1 | pass |
| C44 | `.venv/bin/python -m pytest tests/test_settings.py::test_save_refuses_literal_secret_and_writes_nothing` | 4 | 4 | pass |
| C45 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_prints_checks_and_exit_code` | 1 | 1 | pass |
| C46 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_db_down_skips_dependents` | 1 | 1 | pass |
| C47 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_dims_mismatch` | 1 | 1 | pass |
| C48 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_chat_check_and_none_configured` | 1 | 1 | pass |
| C49 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_schema_missing` | 1 | 1 | pass |
| C50 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_doctor_pool_lifetime_warning` | 1 | 1 | pass |
| C51 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_use_writes_context` | 1 | 1 | pass |
| C52 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_context_precedence_flag_env_file_default` | 1 | 1 | pass |
| C53 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_delete_user_never_from_context` | 1 | 1 | pass |
| C54 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_bad_context_file_is_error` | 1 | 1 | pass |
| C55 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_c_optional_discovery_and_no_yaml_message` | 1 | 1 | pass |
| C56 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_discovered_yaml_folder_resolves_source_paths` | 1 | 1 | pass |
| C57 | `.venv/bin/python -m pytest tests/test_cli.py::test_cli_without_database_url_names_key` | 3 | 3 | pass |
| C57 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_cli_without_embedder_source_names_key` | 1 | 1 | pass |
| C58 | `.venv/bin/python -m pytest -q` | 102 | 102 | pass |
| C59 | `.venv/bin/python -m pytest tests/test_packaging.py::test_readme_quickstart_commands` | 1 | 1 | pass |
| C60 | `.venv/bin/python -m pytest tests/e2e/test_pr2_init.py::test_pr2_demo` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| env layer placed above code layer | `src/memhub/settings.py:300` | yes |
| types null keeps the type instead of removing it | `src/memhub/settings.py:228` | yes |
| config set no longer refuses a literal secret key | `src/memhub/settings.py:417` | yes |
| pool warning bound >= 3600 -> > 3600 | `src/memhub/cli_setup.py:271` | yes |
| context file beats MEMHUB_WORKSPACE | `src/memhub/cli.py:97` | yes |
