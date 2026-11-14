# search-filters proofs

**HEAD**: 9e44ebf
**Tree**: clean
**Result**: 39 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "prefix_through_search_and_get_all"` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "areas_by_key_or_title"` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "never_returns_another_owners_rows"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_subjects.py tests/e2e/test_subjects_demo.py` | 30 | 30 | pass |
| C15 | `git diff --exit-code 65233c5 -- tests/test_subjects.py tests/e2e/test_subjects_demo.py` | count unknown | - | pass |
| C16 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "entities_and_entity_type_are_anded"` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_search_sql.py` | 2 | 2 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "invalid_filter_raises_before_any_call"` | 14 | 14 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "types_given_twice"` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "tiers_follow_the_entities_order"` | 1 | 1 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "matched_on_names_the_entity_that_matched"` | 1 | 1 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "no_entity_predicate_no_matched_on"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "widen_keeps_the_other_top_level_keys"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "widen_needs_top_level_entities"` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "guard_counts_positive_branches_only"` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_search_filter"` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_entity_flags"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "cli_bad_filter_exits_1"` | 8 | 8 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "tools_take_filters"` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "tools_report_bad_filters"` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "guard_holds_without_filter_everywhere"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "tool_schemas_declare_filters"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_mcp_server.py -k "tools_and_their_schemas"` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "docs_reference_the_grammar"` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "sqlite_borrowed_connection_content_filter"` | 1 | 1 | pass |
| C35 | `.venv/bin/python -m pytest tests/test_search_filters.py -k "naive_datetime_is_utc"` | 1 | 1 | pass |
| C36 | `.venv/bin/python -m pytest -q` | 256 | 256 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
