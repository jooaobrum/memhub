# MCP server and Claude Code source - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/mcp-claude-code/plan.md`

15 checks in 3 slices · 3 one-way doors · 0 open

## Checks

### S1 - The MCP server

**C1** - An MCP client lists exactly `search_memory` (required `query`, optional `type` with the configured types as its enum) and `propose_memory` (required `type`, `fields`; optional `evidence`, `scope` with the configured scopes) (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_tools_and_their_schemas`

**C2** - `search_memory` returns JSON of the hits with exactly the keys `id`, `type`, `content`, `verified`, `scope`, `as_of`, `inferred`: the caller's user memory and the workspace's active memory; not bob's user memory, not a candidate (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_search_memory`

**C3** - `propose_memory` stores a candidate with `run_id` starting `mcp-`, `agent_id == "mcp"`, and returns exactly `{"id": <memory_id>, "event": "PENDING", "status": "candidate"}` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_propose_memory`

**C4** - An unknown type and fields that fail validation come back as `{"rejected": ...}`; a call missing `query` comes back with `is_error`; a call after each still works (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_invalid_calls_do_not_stop_the_server`

**C5** - Workspace and user follow door 3: `--ws` beats `MEMHUB_WORKSPACE`, which beats `memhub use`; with nothing set, cwd `/a/b c.d` gives workspace `-a-b-c-d` and the OS user; each process gets its own `mcp-` run id (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_identity`

**C6** - `memhub mcp` exits 1 before serving, the reason on stderr, when the config has no embedder, when the ledger has no tables, and when the `mcp` package is missing (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_fails_before_serving`

**C7** - The docs show `claude mcp add memhub -- memhub mcp`, the workspace rule and that proposals wait in `memhub queue` (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_docs`

### S2 - The Claude Code source

**C8** - A fixture session yields, in file order, one `Interaction` per user or assistant line with text: a string user message, the text block of an assistant line that also has `thinking` (not the thinking), with `thread_id = sessionId`, `workspace_id` = project folder, `message_id = uuid`, aware `timestamp`; a tool-result-only user line and a tool-use-only assistant line yield nothing (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_messages_and_their_ids`

**C9** - `isMeta`, sidechain and `<synthetic>` lines and each of the six wrapper prefixes yield nothing; a user text with `<` later in it is kept (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_what_is_left_out`

**C10** - A file with a non-JSON line, a JSON array line and a line without `sessionId` counts 3 in `skipped` and still yields the good lines after them (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_bad_lines_are_skipped_one_by_one`

**C11** - `projects: ["*alpha*"]` reads only matching folders; a `path` that does not exist makes `memhub ingest --source claude_code` exit 1 naming the path (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_project_globs_and_a_missing_folder`

**C12** - `memhub ingest --source claude_code` with no `sources:` entry ingests the default folder; a second run over the same files creates nothing and processes no segment (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_claude_code_source.py::test_ingest_with_the_defaults_twice`

### S3 - The demo

**C13** - On SQLite: ingest of fixture sessions, approval of the workspace candidate, then `memhub mcp` as a stdio subprocess started in the project's folder answers `search_memory` with the approved memory first (AC 13)
Proof: `.venv/bin/python -m pytest tests/e2e/test_pr7_claude_code.py::test_ingest_sessions_then_search_over_mcp`

**C14** - `mcp` is an extra and in `dev`, not a core dependency (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py::test_mcp_is_an_extra`

**C15** - The full suite stays green (AC 14, N1)
Proof: `.venv/bin/python -m pytest -q tests`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| Landing doors (3) | server and dependency C1 C6 C14 · source and its ids C8 C9 · identity C5 | - |
| tools (2) | `search_memory` C1 C2 · `propose_memory` C1 C3 | - |
| tool errors (3) | unknown type C4 · invalid fields C4 · missing argument C4 | - |
| identity order (4) | flag C5 · env C5 · `memhub use` C5 · folder default C5 | - |
| startup failures (3) | no embedder C6 · no tables C6 · no `mcp` package C6 | - |
| line kinds kept (2) | user text C8 · assistant text C8 | - |
| left out (12) | tool result C8 · tool use C8 · thinking C8 · isMeta C9 · sidechain C9 · synthetic C9 · `<command-` C9 · `<local-command-` C9 · `<task-notification>` C9 · `<system-reminder>` C9 · `<browser_instruction>` C9 · `[Request interrupted` C9 | - |
| bad lines (3) | not JSON C10 · not an object C10 · missing field C10 | - |
| startup config: `claude_code` source (2 assemblies) | no yaml entry C12 · `kind: claude_code` entry C11 | - |

## Swept

- validation: C4 (tool arguments), C10 (transcript lines)
- failure modes: C6 (refuse before serving), C10 (per line, not per file)
- idempotency: C12 (watermark per session)
- authorization: C2 (another user's memory, candidates hidden); C3 (proposals are candidates, D6 roles unchanged)
- concurrency: n/a - one stdio server per session; the ledger's own transactions apply
- data lifecycle: C3 (what a proposal stores), C8 (only text is kept)
- dependency failure: C6 (`mcp` missing), C11 (folder missing)
- state transitions: C13 (candidate -> approved -> found)
- observability: C3 (`run_id`, `agent_id` on proposals), C12 (run summary of the ingest)

## Handoff

- **Settled mid-build:** none
- **Dispatched:** none yet
- **Boundary:** none yet
- **Abandoned:** none
- **Walked:** n/a - no screen
