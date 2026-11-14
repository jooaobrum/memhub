# review-edit proofs

**HEAD**: 1608305
**Tree**: clean
**Result**: 35 pass, 0 fail; mutations 3 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "edit_on_review_notes"` | 3 | 3 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "approve_with_fields_edits_and_activates"` | 3 | 3 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "approve_event_add_or_update"` | 3 | 3 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_pos` | 4 | 4 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "fields_that_change_nothing_approve_as_today"` | 9 | 9 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "invalid_fields_leave_the_candidate"` | 9 | 9 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "key_change_on_review"` | 3 | 3 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "replace_with_fields"` | 3 | 3 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "resolve_and_fields"` | 3 | 3 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "user_scope_owner_or_role"` | 6 | 6 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "workspace_scope_needs_role"` | 6 | 6 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "postgres or sqlite or memory"` | 54 | 54 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_review_edit_cli.py -k "approve_set_parses_json"` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/test_review_edit_cli.py -k "approve_set_errors"` | 2 | 2 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "one_field_check_for_every_write"` | 3 | 3 | pass |
| C17 | `.venv/bin/python -m pytest tests/test_review_edit.py -k "embedder_failure_writes_nothing"` | 3 | 3 | pass |
| C18 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "check_fields"` | 3 | 3 | pass |
| C19 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_writes_a_candidate"` | 6 | 6 | pass |
| C20 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_approved_is_active"` | 3 | 3 | pass |
| C21 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "propose_approve_not_allowed_falls_back"` | 3 | 3 | pass |
| C22 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "correction_updates_the_candidate"` | 3 | 3 | pass |
| C23 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "bad_memory_id"` | 3 | 3 | pass |
| C24 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "rejected_repeat"` | 6 | 6 | pass |
| C25 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "tool_contract"` | 9 | 9 | pass |
| C26 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "middleware_signature_unchanged"` | 1 | 1 | pass |
| C27 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "interrupt_describes_the_memory"` | 1 | 1 | pass |
| C28 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "approve_or_edit_makes_it_active"` | 2 | 2 | pass |
| C29 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "invalid_edit_is_asked_again"` | 1 | 1 | pass |
| C30 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "reject_writes_nothing"` | 1 | 1 | pass |
| C31 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "stream_event"` | 1 | 1 | pass |
| C32 | `.venv/bin/python -m pytest tests/test_example_review_inline.py -k "public_imports_only"` | 1 | 1 | pass |
| C33 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "langchain_pins"` | 1 | 1 | pass |
| C34 | `.venv/bin/python -m pytest tests/test_review_propose.py -k "rejected_repeat"` | 6 | 6 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub examples/review_inline` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| C11 any user may review a user-scope candidate (owner test dropped) | `src/memhub/service.py:277` | yes |
| C8 key compared raw, so "1" -> 1 counts as a key change | `src/memhub/service.py:297` | yes |
| C24 rejected-repeat threshold >= -> > | `src/memhub/service.py:718` | yes |
