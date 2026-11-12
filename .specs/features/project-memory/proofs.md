# project-memory proofs

**HEAD**: eeda1e7
**Tree**: dirty - docs/img/render.py, docs/img/logo.html, docs/img/logo.svg
**Result**: 45 pass, 0 fail; mutations 3 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_yaml_names_the_file` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_env_and_code_name_the_file` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_missing_file` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_unknown_frontmatter_key` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_key_in_both_places` | 3 | 3 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_instructions_in_yaml_with_an_empty_body_load` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_no_frontmatter_and_empty_body` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_config_show_names_the_memory_file` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_bad_yaml_frontmatter` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_template_declares_fields` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_unknown_placeholder_type` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_placeholder_twice` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_optional_sections_render` | 3 | 3 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_written_memory_content_uses_sections` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_literal_brackets_and_plain_templates_render` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_builtin` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_plain_string_type` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_area_short_form` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_area_short_form_edges` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_type_short_forms.py::test_long_forms_unchanged` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_strict_expands` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/e2e/test_pr9_memory_file.py::test_strict_drops_off_domain` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_memory_file.py::test_strict_keeps_threshold` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_settings.py::test_preset_is_refused` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_packaging.py::test_wheel_has_no_presets` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_preset_flag_is_gone` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_settings.py::test_starter_types` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_bare_memory_uses_starter_types` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_settings.py::test_empty_types_refused` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_memory_api.py::test_add_without_type_needs_fact` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_flags_writes_memory_file` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_asks_what_to_keep` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_with_memory_path` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_cli_setup.py::test_init_keeps_memory_file` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_examples.py::test_examples_match_base` | 3 | 3 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_docs.py::test_memory_docs_exist` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_docs.py::test_memory_blocks_load` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_docs.py::test_no_preset_left` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_docs.py::test_memory_docs_name_no_vendor` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_docs.py::test_doc_commands_exist` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_docs.py::test_quickstart_order_and_birds_guide` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/e2e/test_pr9_memory_file.py::test_birds_demo` | 1 | 1 | pass |
| C35 | `.venv/bin/python -m pytest tests/test_e2e_record.py::test_rerun_recorded` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest tests/test_e2e_record.py::test_s4_birds_recorded` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| optional section with an empty list placeholder is rendered (empty list no longer counts as empty) | `src/memhub/types.py:218` | yes |
| memory file named by the lowest layer setting memory: wins instead of the highest | `src/memhub/settings.py:346` | yes |
| area title lowercases the rest of the key (DNA_samples -> Dna samples) | `src/memhub/config.py:150` | yes |
