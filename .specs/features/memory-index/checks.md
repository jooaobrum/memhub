# Memory index - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/memory-index/plan.md`

30 checks in 5 slices · 4 one-way doors · 0 open

Run from the worktree with `PYTHONPATH=src` exported, so the tests import this tree's `src`. The Postgres contract leg needs Docker (the `pg_dsn` fixture).

## Checks

### S1 - the index as data

**C1** - `Memory.index(workspace_id="w1", user_id="u1")` returns `me` with Lubrication 3, Bearings 2 and `team` with Lubrication 1, each group holding `areas`, `subjects`, `types` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_two_groups"`

**C2** - Of u1's Lubrication rows (3 active, 1 archived, 1 rejected, 1 candidate, 1 flag, 1 stale, 1 memory with an active-then-superseded old version and an active new version), the index counts 4; on every backend the index rows port returns only active, non-stale rows (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_counts_only_active"`
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "test_index_rows_visible"`

**C3** - u2's 5 user-scope Lubrication rows change neither u1's `me` (3) nor `team` (1) (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_other_users_not_counted"`

**C4** - A fact naming machine PRD_1 and PRD_2 adds 1 to each and 1 to `fact`; a fact naming PRD_1 twice adds 1 to PRD_1 (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_subject_counted_once_per_distinct"`

**C5** - `types` never lists `area` or `term`, and their rows are in no count (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_skips_internal_types"`

**C6** - With `patient` guarded and `show_guarded: []` no `patient` subject is listed while its rows still count in `areas` and `types`; with `show_guarded: [patient]` `patient p1 (2)` is listed (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_guarded_subjects"`

**C7** - Areas, subjects and types are ordered by count descending then name ascending, and an area with no counted row is absent (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_order_and_empty_areas"`

**C8** - With `user_id=None` the index has `me` None and the team group filled (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_without_user"`

**C9** - The index rows port returns the same visible rows (own user scope plus workspace scope, active, non-stale, no other workspace) on in-memory, SQLite (sqlite-vec and fallback) and Postgres (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "test_index_rows_visible"`

### S2 - the index as text

**C10** - `render(None)` lists `## Areas` (`- lubrication.md: Lubrication (3 memories)`), then `## Subjects` (`- machine PRD_1 (2)`), then `## Types` (`- fact (3)`), with `<!-- no subjects yet -->` when there is none (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_readme_lists_subjects_and_types"`

**C11** - The index text is `<memory_index>`, `Yours (users/u1):`, `Team (team/w1):`, the `Areas:` / `Subjects:` / `Types:` lines in Landing's form, the closing instruction line and `</memory_index>`; a group with nothing listed is left out (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_groups"`

**C12** - With 2,000 machines and `max_chars: 1200` the text is at most 1200 characters and its Subjects line ends `· and N more` with N = 2000 minus the subjects shown; an area title longer than the budget is left out whole and counted in `and 1 more` (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_budget"`

**C13** - With `show: [areas]` neither the index text nor the README has a Subjects or Types section (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_show_sections"`

**C14** - No memory's content appears in the index text (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_text_has_no_content"`

### S3 - the middleware block

**C15** - On a thread's first turn the system prompt holds `<memory_index>` right after `</about_the_user>`, and first when there is no `<about_the_user>` (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_after_about_the_user"`

**C16** - With only type counts to show, no `<memory_index>` is injected (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_no_block_without_areas_or_subjects"`

**C17** - With `index.enabled: false`, no `<memory_index>` is injected even with areas (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_no_block_when_disabled"`

**C18** - A memory added between turns of one thread leaves the turn-1 `<memory_index>` unchanged in turn 2 (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_fixed_for_thread"`

**C19** - `memhub_injected` is the same with the index on and off (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_block_injects_no_ids"`

**C20** - A LangChain agent whose scripted model calls `read_memory_page(area="lubrication")` once gets every active Lubrication line of the user in that tool result (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_agent_opens_area_in_one_call"`

### S4 - tools on every surface

**C21** - `MemoryToolkit.tool_specs()` names `search_memory`, `propose_memory`, `list_memory_index`, `read_memory_page`, and `memhub mcp` lists the same four with the same descriptions (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_toolkit_specs_list_four"`
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_tools_and_their_schemas"`

**C22** - `list_memory_index` returns `{"text", "index"}` equal to the index text and data at the call, and a memory added after a first call shows in the second (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_list_memory_index_tool"`
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_list_memory_index_and_read_memory_page"`

**C23** - `read_memory_page(area="lubrication")` returns `render("lubrication")`; `owner="team"` the workspace page; `subject="prd-1"` that subject's page (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_read_memory_page_tool"`
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_list_memory_index_and_read_memory_page"`

**C24** - `read_memory_page(area="nope")` returns `{"error": "no area 'nope'"}`, an unknown subject an `{"error"}`, `owner="admin"` `{"error": "owner must be me or team"}`, none raising; the middleware tool returns the same messages as text (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_read_memory_page_errors"`

**C25** - The MCP server's instructions name `list_memory_index` (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_mcp_server.py -k "test_instructions_name_the_index"`

### S5 - CLI, config and docs

**C26** - `memhub index --ws w1 --user u1` prints both groups with no budget cut and exits 0; `--json` prints the index data; with no user it prints the Team group only (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_cli_index"`

**C27** - `memhub index` against a config whose database is unusable prints an error on stderr and exits 1 (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_cli_index_fails"`

**C28** - `index.show: [areas, people]`, `index.max_chars: 199` and `index.show_guarded: [ghost]` (undeclared) each raise `ConfigError` naming the key; 200 loads (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_index_config_rejects"`

**C29** - `memhub config show --all` lists `index.enabled`, `index.max_chars`, `index.show`, `index.show_guarded` (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_memory_index.py -k "test_config_show_lists_index"`

**C30** - `docs/integration.md` names `<memory_index>`, `docs/claude-code.md` names `list_memory_index` and `read_memory_page`, `docs/subjects.md` names `show_guarded`, and the docs suite passes (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_docs.py -k "test_index_docs or test_doc_commands_exist"`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| row states left out of counts (7) | archived C2 · rejected C2 · candidate C2 · flag C2 · stale C2 · superseded C2 · other user C3 | - |
| backends (4 legs) | in-memory C9 · SQLite sqlite-vec C9 · SQLite fallback C9 · Postgres C9 | - |
| index sections (3) | areas C1 · subjects C4 · types C5 | - |
| `index.show` members (3) | areas C13 · subjects C13 · types C13 | - |
| config keys (4) | `enabled` C17, C29 · `max_chars` C12, C28, C29 · `show` C13, C28, C29 · `show_guarded` C6, C28, C29 | - |
| surfaces (5) | middleware block C15 · toolkit C21 · MCP C21, C22 · middleware tool C20, C24 · CLI C26 | - |
| AC 12 budget examples (2) | 2,000 machines C12 · title longer than the budget C12 | - |
| AC 4 examples (2) | two subjects C4 · one subject twice C4 | - |
| `Memory.index()` statuses (2) | 200 C1 · 422 C8 | - |
| `memhub index` statuses (2) | 200 C26 · 500 C27 | - |
| `list_memory_index` statuses (2) | 200 C22 · 500 C22 | - |
| `read_memory_page` statuses (3) | 200 C23 · 404 C24 · 422 C24 | - |
| `<memory_index>` block statuses (2) | 200 C15 · 204 C16, C17 | - |
| Landing doors (4) | config section C28, C29 · tool names and arguments C21 · prompt and README text C10, C11 · CLI command C26 | - |

- Claims naming a status or response shape: C22, C23, C24, C26, C27 - each proof crosses the tool or CLI boundary
- C8's 422 is the existing `CallContext` check for a missing workspace (AD-001); C8 proves the `me = None` branch and that a missing workspace still raises

## Swept

- validation: C28
- failure modes: C24, C27
- idempotency: n/a - read only; the index changes nothing it reads
- authorization: C3, C6
- concurrency: n/a - read only, one unit of work per call, no shared state written
- data lifecycle: C2, C18
- dependency failure: C27
- state transitions: n/a - nothing moves through states
- observability: C19

## Handoff

- **Settled mid-build:** user: "fix, commit and push" - push authorized before the Verifier ran
- **Dispatched:** none - Verifier not run yet (push asked first)
- **Boundary:** C1-C30 closed at `e42f6c0`; proofs 35/35 at that HEAD
- **Abandoned:** none
- **Full suite:** every file green except `test_config.py` and `test_terms.py`, which need an untracked repo-root `memhub.yaml` (green with it copied in); `test_upsert.py` slow on Postgres (over 7 min), passing when run alone
