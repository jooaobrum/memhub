# review-per-type proofs

**HEAD**: 9da53e1
**Tree**: clean
**Result**: 35 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "default_rule_is_todays"` | 1 | 1 | pass |
| C1 | `.venv/bin/python -m pytest tests -q -x` | 1035 | 1035 | pass |
| C1 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "old_tests_unchanged"` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "needs_review_table"` | 18 | 18 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "type_true_user_scope_waits"` | 2 | 2 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "type_false_is_active"` | 2 | 2 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_all_user_scope_waits"` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_none_workspace_active"` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "supersede_follows_rule"` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "remember_follows_rule"` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "conflict_waits_whatever_review"` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "verified_row_protected"` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "proposal_always_candidate"` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "hand_add_unchanged"` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "checks_still_run"` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "user_candidate_not_served"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "queue_lists_user_candidates"` | 1 | 1 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "queue_filter_by_user"` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "approver_approves_user_candidate"` | 1 | 1 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "owner_cannot_approve"` | 1 | 1 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "same_statement_merges_into_pending"` | 1 | 1 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "events_follow_status"` | 2 | 2 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "auto_mark_on_routed_active"` | 1 | 1 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "auto_mark_only_from_route"` | 1 | 1 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_review_from_each_layer"` | 1 | 1 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "bad_review_value"` | 1 | 1 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "review_in_both_files"` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "builtin_mapping"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "template_mapping"` | 1 | 1 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "mapping_schema_keys_clash"` | 5 | 5 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "config_show_prints_rule"` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_cli_setup.py -k "test_init_flags_writes_memory_file"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_review_per_type.py -k "docs_state_the_rule"` | 1 | 1 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
