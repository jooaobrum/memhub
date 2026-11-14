# Review per type - checks

Mutations: 3
Build: `.venv/bin/python -m compileall -q src/memhub`
Plan: `.specs/features/review-per-type/plan.md`

32 checks in 6 slices · 3 one-way doors · 0 open

Run with `PYTHONPATH=src` exported, so the tests import this tree's `src`. `tests/test_review_per_type.py` runs on the in-memory backend (SQLite where a CLI needs a file) with fake models and no Docker; the Postgres contract leg needs Docker (the `pg_dsn` fixture).

## Checks

### S1 - one rule decides who waits, in both scopes

**C1** - With no `review` anywhere, an extracted user-scope `fact` is `active` and a workspace-scope `fact` is a `candidate`; the whole suite passes, and no test file other than `tests/test_review_per_type.py`, the starter assertion of `tests/test_cli_setup.py` (C31) and the `"review"` values of `tests/fixtures/examples_resolved_base.json` (`true` -> `null`, the new unset default; user-approved mid-build) differs from the branch base (AC 1)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "default_rule_is_todays"`
Proof: `.venv/bin/python -m pytest tests -q -x`
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "old_tests_unchanged"`

**C2** - `Config.needs_review(type, scope)` returns, over 3 project values × 3 type values (unset, `true`, `false`) × 2 scopes, `true` exactly when: type `true`; or type unset and project `all`; or type unset, project `workspace` and scope `workspace` (AC 8)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "needs_review_table"`

**C3** - A `diagnosis` type with `review: true` and `scopes: [user]` writes an extracted memory as `candidate` with project default unset, and still `candidate` under project `review: none` (AC 2)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "type_true_user_scope_waits"`

**C4** - A workspace-scope `profile` with `review: false` is written `active`, with project default unset and under project `review: all` (AC 3)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "type_false_is_active"`

**C5** - Under project `review: all`, an extracted user-scope `preference` (type sets nothing) is a `candidate` (AC 4)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_all_user_scope_waits"`

**C6** - Under project `review: none`, an extracted workspace-scope `fact` (type sets nothing) is `active` (AC 5)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_none_workspace_active"`

**C7** - Superseding an active user-scope `preference` whose type has `review: true` writes the new version as `candidate`, and `search` still returns the old version, `active` (AC 6)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "supersede_follows_rule"`

**C8** - A `/remember` order for a user-scope type with `review: true` writes a `candidate`, and for a workspace type with `review: false` an `active` row (AC 7)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "remember_follows_rule"`

### S2 - what must not change

**C9** - With the type at `review: false` and project `review: none`, a memory contradicting an active user-scope `preference` is a `candidate` with `conflicts_with` = the old memory, its event is `CONFLICT`, and it is in the queue (AC 9)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "conflict_waits_whatever_review"`

**C10** - With the type at `review: false` and project `review: none`, an extraction that would replace a `verified` row opens a conflict, and the verified row stays `active` with unchanged content (AC 10)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "verified_row_protected"`

**C11** - With the type at `review: false` and project `review: none`, `propose_memory` writes a `candidate` with event `PENDING` through `MemoryService.propose`, `MemoryToolkit`, `MemoryMiddleware` and `memhub mcp` (AC 11)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "proposal_always_candidate"`

**C12** - Under project `review: all` and a type at `review: true`, `Memory.add(..., infer=False)` in user scope is `active` and `verified: true`, and `memhub add` gives the same (AC 12)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "hand_add_unchanged"`

**C13** - With the type at `review: false` and project `review: none`, a candidate with an ungrounded quote, one below the admission threshold, and one carrying an injection pattern are dropped with today's reasons, and none is written (AC 13)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "checks_still_run"`

### S3 - user-scope candidates are reviewable and never served

**C14** - A pending user-scope candidate is absent from `Memory.search`, from `Memory.get_all()`, and from the prompt `MemoryMiddleware` injects, for a `retrieval: search` type and a `retrieval: always` type (AC 14)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "user_candidate_not_served"`

**C15** - An extracted user-scope candidate is listed by `MemoryService.queue`, `Memory.queue` (its owner and an approver) and `memhub queue`, after any conflict; `Memory.queue` called by another user with no approving role does not list it (AC 15)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "queue_lists_user_candidates"`

**C16** - With candidates for `U`, for `V` and a workspace candidate, `memhub queue --user U` and `MemoryService.queue(user_id="U")` list only `U`'s (AC 16)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "queue_filter_by_user"`

**C17** - An actor with `workspace_admin` approving a user-scope candidate makes it `active` with `reviewed_by` = that actor, and the next `search` for its owner returns it; rejecting another makes it `rejected` with `reviewed_by` = that actor (AC 17)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "approver_approves_user_candidate"`

**C18** - The candidate's owner (role `user`) approving or rejecting their own user-scope candidate gets `PermissionDenied` from `Memory`, and exit 1 from `memhub approve`; the row stays `candidate` (AC 18)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "owner_cannot_approve"`

**C19** - Extracting the same statement again while its user-scope candidate is pending returns `MERGE`, adds the new evidence to that candidate, and leaves exactly one candidate (AC 19)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "same_statement_merges_into_pending"`

### S4 - what the app and the reviewer see

**C20** - `Memory.add(..., infer=True)` returns `ADD` for an active new row, `UPDATE` for an active new version, and `PENDING` for a candidate written by the rule, in user and workspace scope (AC 20)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "events_follow_status"`

**C21** - On Postgres, SQLite (with sqlite-vec and fallback) and in-memory, `add_memory(..., reviewed_by="auto")` and `edit_memory(..., reviewed_by="auto")` store `auto`, and both store `NULL` when the argument is omitted (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_contract_memory.py tests/test_contract_sqlite.py tests/test_contract_postgres.py -k "reviewed_by_is_stored"`

**C22** - With the default config, an extracted user-scope `fact` is `active` with `reviewed_by: "auto"` and `verified: false`; an active new version gets `auto`; a workspace candidate has `reviewed_by` `NULL`; a merge into an existing row leaves its `reviewed_by` unchanged (AC 21)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "auto_mark_on_routed_active"`

**C23** - `reviewed_by` is never `auto` on a row added by hand (stays `NULL`), approved or rejected (the actor's id) (AC 22)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "auto_mark_only_from_route"`

### S5 - the rule is written once and readable

**C24** - `review: all` set in the `memory.md` frontmatter, in `memhub.yaml`, and in a `Memory(config={...})` dict each gives `Config.review == "all"`; with none set it is `"workspace"` (AC 23)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "project_review_from_each_layer"`

**C25** - `review: maybe` and `review: true` at project level raise `ConfigError` naming `none, workspace, all`, and `memhub queue` / `memhub config show` exit 1 with that message; `review: "yes"` on a type raises `ConfigError` (AC 24)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "bad_review_value"`

**C26** - `review` set in both `memhub.yaml` and its `memory.md` raises `ConfigError` "review is set in both memhub.yaml and memory.md: keep one" (AC 25)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "review_in_both_files"`

**C27** - `profile: {builtin: true, review: false}` resolves to the built-in profile's class and fields with `review: false`; `{builtin: true, retrieval: always}` keeps `always`; `sighting: {builtin: true}` raises the same `ConfigError` as `sighting: builtin` (AC 26)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "builtin_mapping"`

**C28** - `fix: {template: "{fault}: {action}", review: true}` resolves to fields `fault`, `action`, `content_template` `"{fault}: {action}"` and `review: true` (AC 27)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "template_mapping"`

**C29** - A type mapping holding `builtin: true` with `template`, `builtin: true` with `fields`, `template` with `fields`, `template` with `class`, or `builtin: false` raises `ConfigError` naming the type and the keys (AC 28)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "mapping_schema_keys_clash"`

**C30** - `memhub config show` prints `review  workspace  default` and `review.fact  workspace  default` for the default config, and `review.diagnosis  all  memory.md` / `review.profile  none  memory.md` when memory.md sets them `true` / `false`, without `--all` (AC 29)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "config_show_prints_rule"`

**C31** - The starter `memory.md` `memhub init` writes has frontmatter `{"review": "workspace", "types": {"fact": "builtin", "preference": "builtin", "profile": "builtin"}}` (AC 30)
Proof: `.venv/bin/python -m pytest tests/test_cli_setup.py -k "test_init_flags_writes_memory_file"`

### S6 - docs

**C32** - `docs/memory.md` names the project `review` key with `none`, `workspace`, `all` and the default, the type `review`, `{builtin: true` and `{template:`, that conflicts and agent proposals always wait, and `reviewed_by: "auto"`; `docs/guides/01-quickstart.md` section 6 names `review`; `docs/memory-setup.md` question 4 asks who approves, and its section 2 shows `review` (AC 31, AC 32, AC 33)
Proof: `.venv/bin/python -m pytest tests/test_review_per_type.py -k "docs_state_the_rule"`

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| `needs_review` input space (18) | C2, table-driven over all 18 | - |
| AC 2 examples (2) | project unset C3 · project `none` C3 | - |
| AC 3 examples (2) | project unset C4 · project `all` C4 | - |
| route write paths (2) | new row C1 C3-C6 C22 · new version C7 C22 | - |
| who triggers routing (2) | extraction C1-C7 · `/remember` C8 | - |
| propose entry points (4) | service C11 · toolkit C11 · middleware C11 · MCP C11 | - |
| hand-add entry points (2) | `Memory.add` C12 · `memhub add` C12 | - |
| pipeline checks kept (3) | grounding C13 · admission C13 · injection scan C13 | - |
| served-to-agent readers (4) | `search` C14 · `get_all` C14 · injection `search` type C14 · injection `always` type C14 | - |
| queue readers (3) | `MemoryService.queue` C15 C16 · `Memory.queue` C15 · `memhub queue` C15 C16 | - |
| events (4) | `ADD` C20 · `UPDATE` C20 · `PENDING` C11 C20 · `CONFLICT` C9 | - |
| `reviewed_by` writers (5) | route active `auto` C22 · route candidate `NULL` C22 · hand add `NULL` C23 · approve actor C17 C23 · reject actor C17 C23 | - |
| backends (4) | Postgres C21 · SQLite with sqlite-vec C21 · SQLite fallback C21 · in-memory C21 | - |
| config layers for `review` (3) | `memory.md` C24 · `memhub.yaml` C24 · code dict C24 | - |
| type forms carrying `review` (3) | long form C3 C30 · `{builtin: true}` C27 · `{template:}` C28 | - |
| mapping clash kinds (5) | C29, table-driven over all 5 | - |
| `review:` project key statuses (2) | 200 C24 · 422 C25 C26 | - |
| `review:` on a type statuses (2) | 200 C27 C28 · 422 C25 C29 | - |
| `memhub queue` statuses (2) | 200 C15 C16 · 422 C25 | - |
| `MemoryService.queue` statuses (2) | 200 C15 C16 · 404 C15 | - |
| approve / reject statuses (2) | 200 C17 · 403 C18 | - |
| `memhub config show` statuses (2) | 200 C30 · 422 C25 | - |
| rows returned statuses (1) | 200 C22 | - |
| Landing doors (3) | 1 `reviewed_by = "auto"` C21 C22 · 2 project rule C24 C25 C26 · 3 type override and short form C27 C28 C29 | - |
| decisions (4) | AD-052 C27 C28 · AD-053 C17 C18 · AD-054 C11 · AD-055 C22 C23 | - |
| docs readers (3) | `docs/memory.md` C32 · guide 01 C32 · `docs/memory-setup.md` C32 | - |

## Swept

- validation: C25, C26, C29
- failure modes: existing - `route()` runs inside the segment's unit of work, so a failed write rolls back as today (`test_push.py`); no new partial write
- idempotency: C19 (same statement again merges into the pending candidate)
- authorization: C15, C18
- concurrency: existing - one ingest worker per thread under the advisory lock (AD-015, AD-016); routing adds no shared state
- data lifecycle: C22 (old rows not backfilled, merge keeps `reviewed_by`); C7 (old version served until the new one is approved)
- dependency failure: n/a - `needs_review` reads config only; no external call added
- state transitions: C3-C9, C17, C18, C19 (the plan's state diagram, every arrow)
- observability: C30 (`config show` states the rule); C22 (`reviewed_by: "auto"` marks what went in without a person)

## Handoff

- **Settled mid-build:** branch - user chose "Worktree off main (Recommended)": `feat/review-per-type` in its own worktree from `main`, search-filters untouched; baseline - asked "tests/fixtures/examples_resolved_base.json stores `\"review\": true` (the old default) on 11 types ... How should I handle it?", user chose "Update baseline + C1 (Recommended)": those 11 become `null`, and C1 allows that fixture to differ in the `review` key only
- **Dispatched:** none yet
- **Boundary:** none yet
- **Abandoned:** none
