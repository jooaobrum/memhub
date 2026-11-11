# mcp-claude-code proofs

**HEAD**: de4bc25
**Tree**: clean
**Result**: 16 pass, 0 fail; mutations 0 killed, 0 not

| Check | Proof | Ran | Passed | Result |
| --- | --- | --- | --- | --- |
| C1 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_tools_and_their_schemas` | 1 | 1 | pass |
| C2 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_search_memory` | 1 | 1 | pass |
| C3 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_propose_memory` | 1 | 1 | pass |
| C4 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_invalid_calls_do_not_stop_the_server` | 1 | 1 | pass |
| C5 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_identity` | 1 | 1 | pass |
| C6 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_fails_before_serving` | 1 | 1 | pass |
| C7 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_docs` | 1 | 1 | pass |
| C8 | `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_messages_and_their_ids` | 1 | 1 | pass |
| C9 | `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_what_is_left_out` | 1 | 1 | pass |
| C10 | `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_bad_lines_are_skipped_one_by_one` | 1 | 1 | pass |
| C11 | `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_project_globs_and_a_missing_folder` | 1 | 1 | pass |
| C12 | `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_ingest_with_the_defaults_twice` | 1 | 1 | pass |
| C13 | `.venv/bin/python -m pytest tests/e2e/test_pr7_claude_code.py::test_ingest_sessions_then_search_over_mcp` | 1 | 1 | pass |
| C14 | `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_is_an_extra` | 1 | 1 | pass |
| C15 | `.venv/bin/python -m pytest -q tests` | 870 | 870 | pass |
| Build | `.venv/bin/python -m compileall -q src/memhub` | - | - | pass |
