# push-ingestion proofs

**HEAD**: 721b444
**Tree**: clean
**Result**: 20 pass, 0 fail; mutations 3 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_push.py::test_push_returns_events_and_drops` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_push.py::test_events_follow_the_reconcile_decision` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_push.py::test_a_new_version_waiting_for_review_is_pending` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_push.py::test_rows_carry_the_call_ids_and_push_evidence` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_push.py::test_segments_by_user_turns_without_idle_wait` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_push.py::test_remember_command_in_pushed_messages` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_push.py::test_infer_defaults_to_false` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_push.py::test_same_messages_twice_cost_nothing` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_push.py::test_only_messages_after_the_watermark` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_push.py::test_message_ids` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_push.py::test_thread_defaults_to_the_run` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_push.py::test_invalid_calls_raise_before_any_work` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_push.py::test_message_validation` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_push.py::test_failed_segment_raises_and_is_retried` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/test_push.py::test_thread_lock` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_push.py::test_extract_error_is_a_drop` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest tests/e2e/test_pr5_push.py::test_push_a_conversation_then_review` | 3 | 3 | pass |
| C16 | `.venv/bin/python -m pytest tests/test_push.py::test_push_docs` | 1 | 1 | pass |
| C17 | `.venv/bin/python -m pytest -q tests` | 861 | 861 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |

## Mutations

| Mutation | Location | Killed |
| --- | --- | --- |
| after_watermark keeps the watermark's own message (off by one) | `src/memhub/pipeline/push.py:106` | yes |
| message id repeat counter always 0 | `src/memhub/pipeline/push.py:91` | yes |
| a new version waiting for review is reported UPDATE, not PENDING | `src/memhub/memory.py:114` | yes |
