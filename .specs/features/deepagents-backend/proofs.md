# deepagents-backend proofs

**HEAD**: 0333915
**Tree**: clean
**Result**: 55 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "ls_root_shows_own_folders"` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "ls_folder_lists_areas_with_rows"` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "other_folders_not_found"` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_pages.py -k "page_header"` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_pages.py -k "only_active_lines"` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_pages.py -k "tag_table"` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_pages.py -k "line_id_prefix"` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_pages.py -k "summaries_over_threshold"` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_pages.py -k "subject_page"` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_pages.py -k "readme_lists_areas"` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_pages.py -k "pending_section_is_yours"` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "grep_is_literal"` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "glob_virtual_paths"` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "read_window"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_pages.py -k "memory_render_api"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "read_window"` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_pages.py -k "team_line_follows_review"` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_pages.py -k "team_candidate_lifecycle"` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_pages.py -k "quote_window"` | 4 | 4 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_pages.py -k "rejected_repeat_refused"` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_pages.py -k "write_decision_table"` | 36 | 36 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_pages.py -k "pending_reason"` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_pages.py -k "edit_is_a_version"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "edit_candidate_appr` | 3 | 3 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "edit_candidate_approve_reject"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_pages.py -k "removal_needs_review"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "archive_candidate"` | 6 | 6 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "archive_candidate"` | 2 | 2 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_pages.py -k "removal_no_review"` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_pages.py -k "id_changed_refused"` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_pages.py -k "ignored_sections"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_pages.py -k "stale_stamp"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "stale_write_and_fresh_edit"` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "edit_old_string_errors"` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "refused_operations"` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_pages.py -k "unquoted_line_is_flag"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_pages.py -k "flag_never_served"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "queue_lists_flags"` | 3 | 3 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "queue_lists_flags"` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_pages.py -k "flag_review"` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "write_result_lines"` | 1 | 1 | pass |
| C35 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "imports_public_only"` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "agent_round_trip"` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests/test_pages.py -k "no_deepagents_needed"` | 1 | 1 | pass |
| C37 | `.venv/bin/python -m pytest tests -q -x --ignore=tests/test_example_deepagents.py` | 1403 | 1403 | pass |
| C38 | `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "folder_template_guards"` | 1 | 1 | pass |
| C39 | `.venv/bin/python -m pytest tests/test_pages.py -k "line_validation"` | 1 | 1 | pass |
| C40 | `.venv/bin/python -m pytest tests/test_pages.py -k "model_failure"` | 1 | 1 | pass |
| C41 | `.venv/bin/python -m pytest tests/test_pages.py -k "same_write_twice"` | 1 | 1 | pass |
| C42 | `.venv/bin/python -m pytest tests/test_pages.py -k "stamp_tracks_content"` | 1 | 1 | pass |
| C43 | `.venv/bin/python -m pytest tests/test_pages.py -k "write_provenance"` | 1 | 1 | pass |
| C44 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "archive_candidate_s` | 3 | 3 | pass |
| C45 | `.venv/bin/python -m pytest tests/e2e/test_deepagents_backend.py -k "test_s"` | 26 | 26 | pass |
| C46 | `.venv/bin/python -m pytest tests/test_pages.py -k "file_tool_framing"` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub examples/deepagents_backend` | - | - | pass |
