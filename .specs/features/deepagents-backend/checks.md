# deepagents backend - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub examples/deepagents_backend`
Plan: `.specs/features/deepagents-backend/plan.md`

44 checks in 8 slices · 5 one-way doors · 0 open

Run with `PYTHONPATH=src` exported. `tests/test_pages.py` (rendering, the page diff, the review decisions, flags) runs on the in-memory backend with fake models and no Docker; the storage rules for flags and edit/archive candidates are in the contract suite (`src/memhub/storage/contract.py`), run on in-memory, SQLite (both vector paths) and Postgres (Docker, the `pg_dsn` fixture). `tests/test_example_deepagents.py` needs the `deepagents` extra and runs a real `create_deep_agent` with a scripted chat model against SQLite.

## Checks

### S1 - an agent reads its memory as pages

**C1** - With the default templates, `u1` in `w1`: `ls /memories/` returns exactly `team/` and `users/`; `ls /memories/team/` exactly `w1/`; `ls /memories/users/` exactly `u1/`; with `team_folder="shared"`, `ls /memories/` returns exactly `shared/` and `users/` (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "ls_root_shows_own_folders"`

**C2** - `ls` on `u1`'s folder with areas `travel` (3 active rows) and `work` (0) returns exactly `README.md` and `travel.md`; on a team folder with no area, exactly `README.md` (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "ls_folder_lists_areas_with_rows"`

**C3** - Called as `u1` in `w1`, `ls` and `read` on `users/u2/`, `team/w2/`, `users/nobody/` and `users/u1/nope.md` each return the same `not found` error and no entries or content (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "other_folders_not_found"`

**C4** - A rendered area page starts with `# Memory: <title> (<owner>)`, then the comment line holding `rendered <ISO UTC>` and `stamp <12 hex>`, then `## How to read this` whose body equals AD-063's fixed text character for character (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "page_header"`

**C5** - An area with one active, one candidate, one rejected, one superseded, one archived and one flag row renders exactly one `## Memory` line, the active one, and stale rows are left out; lines are newest `observed_at` first (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "only_active_lines"`

**C6** - The tag table: approved by `alice` with a quote → `[reviewed]`; `auto`, `assertion = inferred`, with a quote → `[inferred]`; `auto` with a quote → `[said]` and the text shows the quote in `"…"`; `add("text")` (no quote, `auto`) → `[added]`; `reviewed_by` NULL and no quote → `[added]` (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "tag_table"`

**C7** - A line's id is the first 8 hex of its `memory_id`; two rows whose ids share the first 8 hex get the shortest unique prefixes (9+), and a third unrelated row keeps 8 (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "line_id_prefix"`

**C8** - With `render.summarize_over: 30`: 30 active lines render 30 lines in full; 31 lines, 12 about subject `flights`, render one `#s_flights (12 memories) … → travel/flights.md` line and the 19 others in full; `summarize_over: 5` changes the switch point (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "summaries_over_threshold"`

**C9** - `travel/flights.md` renders all 40 active lines of that subject when `summarize_over` is 30; an unknown subject raises `NotFound` (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "subject_page"`

**C10** - `README.md` renders the header, `## How to read this`, and one line per area with its title and active count (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "readme_lists_areas"`

**C11** - `u1`'s page lists `u1`'s candidate as `- [pending] <text>. Proposed <date>.` and `u1`'s flag as `- [flag] <text>. Noted <date>.` under `## Pending review (yours, not usable yet)`; `u2`'s candidate and flag in the same area never appear; with none, the section is absent (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "pending_section_is_yours"`

**C12** - `grep "CDG"` returns the `travel.md` line holding `CDG` with its path and 1-based line number; `grep "C.G"` returns nothing; `grep` with `path` limited to the team folder returns no personal line (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "grep_is_literal"`

**C13** - `glob("**/*.md")` returns every README, area and subject path the caller can see and none of another owner's; `glob("users/*/travel.md")` returns only `users/u1/travel.md` (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "glob_virtual_paths"`

**C14** - `read(offset=2, limit=3)` on a 10-line page returns lines 3-5 with `start_line=3`, `end_line=5`, `total_lines=10`, `next_offset=5`; `limit=0` returns `no_lines_requested=True`; a negative offset reads from line 1; `download_files` on that page returns its rendered text as UTF-8 bytes (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "read_window"`

**C15** - `Memory.render(area, user_id=...)` equals the text the backend's `read` returns for that page (stamp included); `Memory.pages()` / `Memory.page()` equal `service.pages` / `service.page` for the caller; `Memory.render` for another user's area raises `NotFound` (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "memory_render_api"`
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "read_window"`

### S2 - a team line the user said

**C16** - Under project `review: workspace`, a new team line backed by a user quote creates exactly one workspace `candidate` linked `in_area` to that page's area, with that quote as evidence, and the change reads `pending review: <text>`; under the same project, a type with `review: false` is saved `active` with `reviewed_by = "auto"` and reads `saved #<8 hex>: <text>` (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "team_line_follows_review"`

**C17** - That candidate is in `Memory.queue` and `memhub queue`, in the writer's `## Pending review`, not a `## Memory` line; after `memhub approve` it is a `## Memory` line and leaves `## Pending review` (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "team_candidate_lifecycle"`

**C18** - The line check reads only the last `ingestion.remember_context` messages: a quote in a user message inside the window → kept; the same quote only in an assistant message → flag; the quote only in a user message older than the window → flag; a quote that differs by one word → flag (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "quote_window"`

**C19** - A new line close to a candidate a reviewer rejected for the same owner (AD-043) saves nothing and reads `refused: a reviewer rejected this before`; for another owner it is saved (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "rejected_repeat_refused"`

### S3 - the project's review config decides, in both folders

**C20** - The decision table for a quoted new line, over 2 scopes × 3 project `review` values × 3 type values (unset, `true`, `false`) × slot free/taken (36 cases): `active` (`saved …`) exactly when `needs_review(type, scope)` is false and the slot is free, else `candidate` (`pending review: …`) (AC 20, AC 21)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "write_decision_table"`

**C21** - A personal `preference` keyed `seat` while `#3f9a21c0` holds an active `seat` reads `pending review: Window seat on short flights (#3f9a21c0 holds an active value)`; a personal type with `review: true` reads `pending review: <text> (needs review)` (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "pending_reason"`

### S4 - edits and removals

**C22** - An edited line (id kept, quoted new text) writes a candidate with the same `memory_id` and version N+1 when its type and scope need review, and leaves the active row serving; when they need none, the new version is active, the old one `superseded`, and `history` holds both (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "edit_is_a_version"`

**C23** - Approving an edit candidate supersedes the active row with it; rejecting it leaves the active row active and unchanged, on every backend (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "edit_candidate_approve_reject"`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "edit_candidate_approve_reject"`

**C24** - Dropping a line whose memory needs review writes a candidate whose payload is the active payload plus `"_action": "archive"` and reads `pending review: archive #<id>`; approving it archives the memory, rejecting it leaves it active, on every backend (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "removal_needs_review"`
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "archive_candidate"`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "archive_candidate"`

**C25** - Dropping a line whose memory needs no review archives it at once, reads `archived #<id>`, and asks the model nothing (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "removal_no_review"`

**C26** - A line whose id was changed to one not on the page, or to another line's id, changes nothing for that line and reads `refused: line #<id> id changed`; the other lines of the same write are still handled (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "id_changed_refused"`

**C27** - Rewriting the header, the `## How to read this` text, a summary line, or the `## Pending review` section, each alone, writes no row and reads `no change` (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "ignored_sections"`

**C28** - `write_page` with a stamp other than the current page's raises `ValidationError` and writes no row, and the backend's `write` returns `error="page changed since you read it: read it again"`; an `edit` after a reviewer approved a row elsewhere on the page applies to the current page (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "stale_stamp"`
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "stale_write_and_fresh_edit"`

**C29** - `edit` with an `old_string` absent from the page, or present twice without `replace_all`, returns deepagents' edit error and writes no row (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "edit_old_string_errors"`

**C30** - `delete`, `upload_files`, and `write` to an `<area>.md` that does not exist each return `error="areas come from memory.md: edit lines in an existing page"` and write no row (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "refused_operations"`

### S5 - flags

**C31** - A new line and an edited line with no supporting quote each store one `flag` row (door 1: `status = 'flag'`, the folder's scope and owner, `content` = the line, evidence `[{"kind": "trace", "thread_id", "run_id"}]`, `in_area` to the page's area, `created_by` = the user id), read `flag: <text> (no supporting quote)`, and the backend returns `error=None` (AC 31)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "unquoted_line_is_flag"`

**C32** - A flag is never returned by `search`, `get_all(status="active")`, injection or as a `## Memory` line; `Memory.queue` and `memhub queue` list it as `flag`, on every backend (AC 32)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "flag_never_served"`
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "queue_lists_flags"`
Proof: `.venv/bin/python -m pytest tests/test_contract_postgres.py -k "queue_lists_flags"`

**C33** - `memhub approve <flag>` exits 1 with `a flag is not a memory: reject it to dismiss it` and leaves it a flag; `memhub reject <flag>` makes it `archived`; afterwards a proposal with the same text for the same owner is not refused by AD-043 (AC 33)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "flag_review"`

### S6 - the write result

**C34** - One page write holding, in page order, a quoted new personal line, a line with a changed id, an unquoted line and a removed line returns the tool message `Updated file <path>` followed by exactly `saved #…: …`, `refused: line #… id changed`, `flag: … (no supporting quote)`, `archived #…`, one per line, in that order (AC 34)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "write_result_lines"`

### S7 - the example runs a real agent

**C35** - Every `memhub` name the files in `examples/deepagents_backend/` import is in `memhub.__all__`, and no module under `src/memhub` imports `deepagents` (AC 35)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "imports_public_only"`

**C36** - `create_deep_agent` with a scripted chat model, `CompositeBackend(default=StateBackend(), routes={"/memories/": MemhubBackend(...)})` and a SQLite `Memory` reads a team page, writes a line the user said, the test runs `memhub approve` on it, and the agent's next `read_file` shows it as a `## Memory` line (AC 36)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "agent_round_trip"`

**C37** - With `deepagents` absent from the import path, `import memhub` succeeds and `tests/test_pages.py` passes (AC 37)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "no_deepagents_needed"`
Proof: `.venv/bin/python -m pytest tests -q -x --ignore=tests/test_example_deepagents.py`

### S8 - what nobody wrote down

**C38** - Two folder templates that render the same path for a caller raise `ValidationError` when the path is resolved; a template whose rendered id contains `/` or `..` raises `ValidationError` (validation)
Proof: `.venv/bin/python -m pytest tests/test_example_deepagents.py -k "folder_template_guards"`

**C39** - A new line containing a prompt-injection string is refused by the existing injection scan and reads `refused: <reason>`; a line the grounding rules drop for its form (a one-word fact: `too_thin`; `next year`: `relative_time`) reads `refused: <reason>` and saves nothing (validation)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "line_validation"`

**C40** - When the line check's model call raises, the write saves nothing for that line, reads `refused: memory check failed, try again`, and the lines already handled in that write stay as they were (dependency failure, failure modes)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "model_failure"`

**C41** - Writing the same page text twice saves the new line once: the second write finds the line on the page with its id and reads `no change` (idempotency)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "same_write_twice"`

**C42** - The stamp changes when any `## Memory` or `## Pending review` line changes and not when only `rendered` time changes (concurrency: the stale guard keys on content)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "stamp_tracks_content"`

**C43** - Every row a file-view write creates carries `run_id` and `agent_id = "deepagents"`, and `created_by` = the runtime's `user_id` (observability)
Proof: `.venv/bin/python -m pytest tests/test_pages.py -k "write_provenance"`

**C44** - `memhub reject` on an archive candidate leaves the memory active; `memhub approve` on an archive candidate whose memory was archived meanwhile raises `ValidationError` and changes nothing (state transitions)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py -k "archive_candidate_stale"`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| `Memory.render` statuses (2) | 200 C4 C15 · 404 C9 C15 | - |
| `Memory.pages` / `Memory.page` statuses (2) | 200 C15 · 404 C15 | - |
| `Memory.write_page` statuses (3) | 200 C16 C34 · 409 C28 · 404 C3 | - |
| `MemhubBackend` (`users/{user_id}`, `team/{workspace_id}`) statuses (4) | 200 C1 C34 · 404 C3 · 409 C28 · 422 C30 | - |
| backend protocol methods (9) | `ls` C1 C2 · `read` C14 · `grep` C12 · `glob` C13 · `write` C28 C34 · `edit` C28 C29 · `delete` C30 · `upload_files` C30 · `download_files` C14 | - |
| AC 1 examples (2) | default templates C1 · `team_folder="shared"` C1 | - |
| AC 2 examples (2) | user with `travel` and empty `work` C2 · team with no area C2 | - |
| AC 3 not-found paths (4) | another user C3 · another workspace C3 · missing user C3 · missing area C3 | - |
| AC 5 statuses not rendered (5) | candidate C5 · rejected C5 · superseded C5 · archived C5 · flag C5 | - |
| tag rule, AC 6 (5) | `[reviewed]` C6 · `[inferred]` C6 · `[said]` C6 · `[added]` auto C6 · `[added]` NULL C6 | - |
| AC 8 examples (3) | 30 lines C8 · 31 lines C8 · other threshold C8 | - |
| page diff classes (5) | new C16 C20 · edited C22 · removed C24 C25 · id changed C26 · ignored C27 | - |
| ignored sections (4) | header C27 · how-to-read C27 · summary line C27 · pending section C27 | - |
| AC 18 quote examples (4) | user in window C18 · assistant only C18 · user outside window C18 · one word off C18 | - |
| review decision for a new line (36) | C20, table-driven over all 36 | - |
| AC 16 examples (2) | `review: workspace` C16 · type `review: false` C16 | - |
| AC 21 reasons (2) | slot taken C21 · needs review C21 | - |
| edit outcomes (2) | waits C22 C23 · active at once C22 | - |
| removal outcomes (2) | archive candidate C24 · archived at once C25 | - |
| result kinds (6) | `saved` C16 C34 · `pending review` C16 C21 · `archived` C25 C34 · `flag` C31 C34 · `refused` C19 C26 C34 · `no change` C27 C41 | - |
| readers that must not serve a flag (4) | `search` C32 · `get_all` C32 · injection C32 · `## Memory` C5 C32 | - |
| queue readers (2) | `Memory.queue` C17 C32 · `memhub queue` C17 C32 | - |
| backends (4) | in-memory C23 C24 C32 C44 · SQLite with sqlite-vec C23 C24 C32 C44 · SQLite fallback C23 C24 C32 C44 · Postgres C23 C24 C32 | - |
| flag review actions (2) | approve refuses C33 · reject archives C33 | - |
| Landing doors (5) | 1 flag row C31 C32 C33 · 2 edit/archive candidates C22 C23 C24 C44 · 3 page format and `Memory` calls C4 C6 C7 C8 C10 C11 C15 · 4 folder templates C1 C3 C38 · 5 extra and example place C35 C37 | - |
| decisions (5) | AD-061 C31 C33 · AD-062 C22 C24 · AD-063 C4 C6 C7 C8 C28 · AD-065 C16 C20 C25 C1 · AD-043 C19 C33 | - |
| startup assemblies (2) | `create_deep_agent` with `CompositeBackend` C36 · direct backend in tests C1-C14 | - |

## Swept

- validation: C38, C39
- failure modes: C40
- idempotency: C41
- authorization: C3, C11, C15 (only the caller's folders and pending items); reviewer roles: existing - `service._authorize_review` (AD-053, AD-057)
- concurrency: C28, C42 (a stale whole-file write is refused by content stamp); two writes in the same moment: existing - each change is its own unit of work, as `propose` today
- data lifecycle: C22, C23 (history keeps every version); `delete_user`: existing - it erases every row of the user, flags included
- dependency failure: C40
- state transitions: C23, C24, C33, C44
- observability: C43

## Handoff

- **Settled mid-build:** C39 had no content length limit to prove; asked "What should C39's second half prove instead?" - user chose "A grounding drop (Recommended)": a line dropped for its form reads `refused: <reason>`
- **Dispatched:** none
- **Boundary:** none yet
- **Abandoned:** none
- **Deviations:** C15 gained a second proof (`read_window` asserts the backend serves `Memory.render`), since `tests/test_pages.py` must run without deepagents (C37); the plan's Surface gained a "Built" note: `personal` selects the folder (AD-001 keeps `user_id` as the caller)
