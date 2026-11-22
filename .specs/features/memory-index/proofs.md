# memory-index proofs

**HEAD**: e42f6c0
**Tree**: clean
**Result**: 35 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_two_groups"` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_counts_only_active"` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_other_users_not_counted"` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_subject_counted_once_per_distinct"` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_skips_internal_types"` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_guarded_subjects"` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_order_and_empty_areas"` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_without_user"` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_readme_lists_subjects_and_types"` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_groups"` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_budget"` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_show_sections"` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_has_no_content"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_after_about_the_user"` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_no_block_without_areas_or_subjects"` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_no_block_when_disabled"` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_fixed_for_thread"` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_injects_no_ids"` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_agent_opens_area_in_one_call"` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_toolkit_specs_list_four"` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_tools_and_their_schemas"` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_list_memory_index_tool"` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_list_memory_index_and_read_memory_page"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_read_memory_page_tool"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_list_memory_index_and_read_memory_page"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_read_memory_page_errors"` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_instructions_name_the_index"` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_cli_index"` | 2 | 2 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_cli_index_fails"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_config_rejects"` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_config_show_lists_index"` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_docs.py -k "test_index_docs or test_doc_commands_exist"` | 2 | 2 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
